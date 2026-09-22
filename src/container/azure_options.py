"""Validate the shared host and sandbox Azure setup contract."""
from dataclasses import dataclass
import re

CLOUDS = {'AzureCloud': 'login.microsoftonline.com', 'AzureChinaCloud': 'login.chinacloudapi.cn'}


@dataclass
class Options:
    interactive: bool = False
    cloud: str = ''
    tenant: str = ''
    subscription: str = ''
    tenant_only: bool = False


def parse(args):
    options = Options()
    seen = set()
    index = 0
    while index < len(args):
        flag = args[index]
        if flag in seen or flag not in ('--interactive', '--cloud', '--tenant', '--subscription', '--tenant-only'):
            raise ValueError('Use setup azure [--interactive] [--cloud CLOUD] [--tenant TENANT] [--subscription SUBSCRIPTION | --tenant-only].')
        seen.add(flag)
        key = flag[2:].replace('-', '_')
        if flag in ('--interactive', '--tenant-only'):
            setattr(options, key, True)
        else:
            index += 1
            if index == len(args) or not args[index] or args[index].startswith('-') or any(ord(c) < 32 for c in args[index]):
                raise ValueError('Supply a nonempty value for each Azure setup option.')
            setattr(options, key, args[index])
        index += 1
    if options.cloud and options.cloud not in CLOUDS:
        raise ValueError('Unsupported Azure cloud. Choose ' + ' or '.join(CLOUDS) + '.')
    if options.tenant and not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]*', options.tenant):
        raise ValueError('Use a tenant ID or tenant domain name.')
    if options.tenant_only and options.subscription:
        raise ValueError('Use --subscription or --tenant-only, not both.')
    return options
