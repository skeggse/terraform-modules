#!/usr/bin/env python

import argparse
import base64
from hashlib import sha256
import itertools
import io
import os.path
from os import makedirs
from pathlib import Path
import shutil
import subprocess
import sys
import traceback
from typing import Optional
import zipfile

import boto3


def pip(*args, **kwargs):
    return subprocess.run([sys.executable, '-m', 'pip', *args], **kwargs).returncode


def npm(*args, **kwargs):
    return subprocess.run(['npm', *args], **kwargs).returncode


def bundle_function(fn_dir: Path) -> io.BytesIO:
    print(f'bundling {fn_dir.name}')
    sources = [fn_dir.glob('*.py'), fn_dir.glob('*.js')]
    if (fn_dir / 'requirements.txt').is_file():
        deps = fn_dir / 'site-packages'
        shutil.rmtree(deps, ignore_errors=True)
        makedirs(deps)
        pip(
            'install',
            '-r',
            'requirements.txt',
            '--target',
            'site-packages',
            cwd=fn_dir,
            check=True,
        )
        sources.append(deps.glob('**/*'))
    if (fn_dir / 'package-lock.json').is_file():
        deps = fn_dir / 'node_modules'
        npm('ci', cwd=fn_dir, check=True)
        sources.append(deps.glob('**/*'))

    bio = io.BytesIO()
    with zipfile.ZipFile(bio, mode='w', compression=zipfile.ZIP_DEFLATED) as f:
        for src in sorted(itertools.chain.from_iterable(sources)):
            zinfo = zipfile.ZipInfo.from_file(src, src.relative_to(fn_dir))
            zinfo.date_time = (1980, 1, 1, 0, 0, 0)
            if not zinfo.is_dir():
                with f.open(zinfo, 'w') as dest, open(src, 'rb') as srcd:
                    shutil.copyfileobj(srcd, dest)
    return bio


def compute_digest(data: bytes) -> bytes:
    h = sha256()
    h.update(data)
    return base64.urlsafe_b64encode(h.digest()).rstrip(b'=')


def execa(*args):
    return subprocess.run(args, stdout=subprocess.PIPE, check=True).stdout


def upload_if_changed(s3, bucket: str, key: str, value: io.BytesIO) -> bool:
    # This will fail of the object doesn't already exist. It should exist,
    # because Terraform should have created it.
    try:
        metadata = s3.head_object(Bucket=bucket, Key=key)['Metadata']
    except s3.exceptions.ClientError as err:
        # The 403 error can happen for deploy scripts running in low-privilege contexts, such as CI.
        if str(err.response['Error']['Code']) not in ('403', '404'):
            raise
        metadata = {}
    prev_digest = next((v for k, v in metadata.items() if k.lower() == 'digest'), None)
    value.seek(0)
    new_digest = compute_digest(value.getvalue()).decode()

    # TODO: why doesn't this work for the myqd watcher?
    if prev_digest != new_digest:
        print(f'updating s3://{bucket}/{key}')
        value.seek(0)

        revision_sha = execa('git', 'rev-parse', 'HEAD').rstrip().decode()
        is_dirty = bool(
            subprocess.run(
                ['git', 'status', '--porcelain=v2'], check=True, stdout=subprocess.PIPE
            ).stdout.strip()
        )
        s3.upload_fileobj(
            Fileobj=value,
            Bucket=bucket,
            Key=key,
            ExtraArgs=dict(
                Metadata=dict(
                    digest=new_digest,
                    revision=revision_sha[:9] + (' (dirty)' if is_dirty else ''),
                )
            ),
        )
        return True
    return False


def main(
    source_dir: str, bucket: str, function: list[str], prefix: Optional[str], region: Optional[str]
):
    if region is None:
        s3 = boto3.client('s3', region_name='us-east-1')
        region = s3.get_bucket_location(Bucket=bucket)['LocationConstraint']
        if region != s3.meta.region_name:
            s3 = boto3.client('s3', region_name=region)
    else:
        s3 = boto3.client('s3', region_name=region)

    n = 0
    code = bundle_function(Path(source_dir).absolute())
    n += upload_if_changed(s3, bucket, os.path.join(prefix or '', function + '.zip'), code)

    print(f'updated {n} objects')


def parse_args():
    parser = argparse.ArgumentParser(
        description='Update the code for a Terraform-provisioned Lambda function'
    )
    parser.add_argument('--bucket', help='The bucket to deploy the code to')
    parser.add_argument('--function', help='The function name')
    parser.add_argument('--prefix', help='The optional prefix in S3', default=None)
    parser.add_argument('--region', help='The override region', default=None)
    parser.add_argument('source_dir', help="The source directory containing the function's code")
    return parser.parse_args()


if __name__ == '__main__':
    try:
        sys.exit(main(**vars(parse_args())))
    except subprocess.CalledProcessError as err:
        traceback.print_exc(limit=0)
        sys.exit(err.returncode)
