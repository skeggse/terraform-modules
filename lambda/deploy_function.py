#!/usr/bin/env python

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import cache
import subprocess
import sys
from typing import Any, Iterable, Optional

import boto3


DEFAULT_ALIAS_NAME = 'current'


def input_percent(prompt: str) -> float:
    while True:
        result = input(prompt + ' ')
        try:
            value = float(result)
        except ValueError:
            continue
        if value < 0 or value > 100:
            continue
        return value / 100


@dataclass(frozen=True)
class Alias:
    primary_version: str
    secondary_version: Optional[str] = None
    secondary_weight: float = 0

    def __str__(self) -> str:
        return ', '.join(
            f'v{version} ({self.get_weight(version) * 100:.1f}%)'
            for version in sorted(self.versions(), reverse=True)
        )

    def get_weight(self, version: str) -> float:
        if version == self.secondary_version:
            return self.secondary_weight
        if version == self.primary_version:
            return 1 - self.secondary_weight
        return 0

    def normalized(self) -> Alias:
        if self.secondary_weight <= 0:
            return Alias(primary_version=self.primary_version)
        if self.secondary_weight >= 1:
            return Alias(primary_version=self.secondary_version)
        return self

    def versions(self) -> Iterable[str]:
        yield self.primary_version
        if self.secondary_version is not None:
            yield self.secondary_version

    @staticmethod
    def from_versions(*versions: tuple[str]) -> Alias:
        assert len(versions) in {1, 2}
        if len(versions) == 1:
            return Alias(primary_version=versions[0])
        return Alias(
            primary_version=versions[0],
            secondary_version=versions[1],
            secondary_weight=1 - input_percent(f'select new traffic weight for {versions[0]}:'),
        ).normalized()

    @staticmethod
    def from_description(description: dict) -> Optional[Alias]:
        primary_version = description['FunctionVersion']
        if primary_version == '$LATEST':
            return None
        additional = description.get('RoutingConfig', dict(AdditionalVersionWeights={}))[
            'AdditionalVersionWeights'
        ]
        pair = next(iter(additional.items()), (None, 0))
        return Alias(
            primary_version=primary_version,
            secondary_version=pair[0],
            secondary_weight=pair[1],
        )


def skip_last(i: Iterable) -> Iterable:
    i = iter(i)
    try:
        prev = next(i)
    except StopIteration:
        return
    for value in i:
        yield prev
        prev = value


def format_arg(k: str, v: Any) -> str:
    assert len(k) > 1, 'no support for single-character args'
    k = k.replace('_', '-')
    if isinstance(v, bool):
        return f'--{k}' if v else f'--no-{k}'
    if isinstance(v, (str, int, float)):
        return f'--{k}={v}'
    raise NotImplementedError('undefined formatting')


def fzf(*args, **kwargs):
    if kwargs.get('height') == 'auto':
        kwargs['height'] = min(20, len(args) + 4)
    proc = subprocess.run(
        ['fzf', *(format_arg(k, v) for k, v in kwargs.items()), '--read0', '--print0'],
        input=b'\0'.join(item.encode() for item in args),
        stdout=subprocess.PIPE,
        check=True,
    )
    return [item.decode() for item in skip_last(proc.stdout.split(b'\0'))]


@cache
def get_versions(client, function_name: str) -> dict[str, str]:
    return {
        version['Version']: version
        for page in client.get_paginator('list_versions_by_function').paginate(
            FunctionName=function_name
        )
        for version in page['Versions']
        if version['Version'] != '$LATEST'
    }


@cache
def get_alias(client, function_name: str) -> Optional[tuple[Optional[Alias], str]]:
    try:
        alias = client.get_alias(FunctionName=function_name, Name=DEFAULT_ALIAS_NAME)
    except client.exceptions.ResourceNotFoundException:
        return None
    return Alias.from_description(alias), alias['RevisionId']


def set_alias(client, function_name: str, alias: Alias, revision_id: str) -> Optional[Alias]:
    try:
        result = client.update_alias(
            FunctionName=function_name,
            Name=DEFAULT_ALIAS_NAME,
            FunctionVersion=alias.primary_version,
            RoutingConfig=dict(
                AdditionalVersionWeights={alias.secondary_version: alias.secondary_weight}
                if alias.secondary_version is not None
                else {}
            ),
            RevisionId=revision_id,
        )
    except client.exceptions.PreconditionFailedException as err:
        if 'revision id' not in err.response['message'].lower():
            raise
        print('update conflict: the alias was updated during this script\'s execution')
        return None
    updated_result = Alias.from_description(result)
    print(f'updated {function_name} to {updated_result}')
    return updated_result


def main(region: Optional[str], function_name: Optional[str]) -> int:
    client = boto3.client('lambda', region_name=region)

    # TODO: use asyncio instead of a thread pool.
    with ThreadPoolExecutor() as executor:
        functions = frozenset(
            name
            for name, has_alias in executor.map(
                lambda func: (func, get_alias(client, func) is not None),
                (
                    func['FunctionName']
                    for page in client.get_paginator('list_functions').paginate()
                    for func in page['Functions']
                ),
            )
            if has_alias
        )

    if function_name is None:
        if not functions:
            print(f'no functions found in {client.meta.region_name}')
            return 0

        function_name = fzf(*sorted(functions), height='auto')[0]

    print(f'deploying {function_name}')
    available_versions = get_versions(client, function_name)
    if len(available_versions) <= 1:
        print('no deployment options available, try deploying another version', file=sys.stderr)
        return 0

    # Show aliased versions first, then all other versions in descending order.
    current_alias, alias_revision_id = get_alias(client, function_name)
    normalized_current_alias = current_alias and current_alias.normalized()
    alias_versions = frozenset([] if current_alias is None else current_alias.versions())
    version_mapping = {
        f'v{v} [{version["Description"] or "<missing>"}]': v
        for v, version in available_versions.items()
    }
    versions_to_show = (
        k
        for k, _ in sorted(
            version_mapping.items(),
            key=(lambda value: (value[1] in alias_versions, int(value[1]))),
            reverse=True,
        )
    )
    selected_versions = [
        version_mapping[entry] for entry in fzf(*versions_to_show, multi=2, height='auto')
    ]
    if len(selected_versions) == 0:
        print('no versions selected for deployment', file=sys.stderr)
        return 0
    selected_versions.sort(key=int, reverse=True)

    print(f'alias `current` configured for {normalized_current_alias}')
    new_alias = Alias.from_versions(*selected_versions)
    if normalized_current_alias == new_alias:
        print('requested traffic routing matches current alias')
        return 0

    if new_alias.secondary_version is None:
        print(f'updating `current` to route all traffic to v{new_alias.primary_version}')
    else:
        print(f'updating `current` to route traffic to {new_alias}')

    return (
        1
        if set_alias(client, function_name, new_alias, revision_id=alias_revision_id) is None
        else 0
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description='Deploy Lambda function alias to a routing configuration'
    )
    parser.add_argument('--region', help="The function's region", default=None)
    parser.add_argument('function_name', help='The function name', default=None, nargs='?')
    return parser.parse_args()


if __name__ == '__main__':
    sys.exit(main(**vars(parse_args())))
