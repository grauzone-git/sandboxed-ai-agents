"""Pass the CLI browser request in memory without placing its URL in argv."""
import json
import os
import sys
import webbrowser


class HostBrowser(webbrowser.BaseBrowser):
    def open(self, url, *args, **kwargs):
        print('SANDBOX_AZURE_BROWSER ' + json.dumps(url), flush=True)
        return True


if __name__ == '__main__':
    # Azure CLI/MSAL uses Python's browser interface. The CLI still owns OAuth,
    # callback validation, token storage, and renewal; this only transports a URL.
    # CLI checks webbrowser.get() before MSAL opens the URL. Registration makes
    # the transport discoverable in a headless image, preventing device fallback.
    # MSAL otherwise explicitly selects installed Edge, bypassing preferred
    # controllers. This preference applies only to the setup child process.
    os.environ['BROWSER'] = 'sandbox-host'
    webbrowser.register('sandbox-host', None, HostBrowser(), preferred=True)
    from azure.cli.core import get_default_cli
    sys.exit(get_default_cli().invoke(sys.argv[1:]))
