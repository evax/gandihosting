Gandi Hosting resources
=======================

This is a small library intended to be used with ipython.

You need a configuration file:

    [hosting]
    uri = https://rpc.gandi.net/xmlrpc/2.0/
    key = XXXXXXXXXXXXXXXXXXXXXXXX

then you can

    from gandi_hosting import *
    gh = GandiHosting.from_config('myconfig.cfg')

now a few examples

add memory to a vm

    myvm = gh.vms['myvmhostname']
    myvm.memory = 1024

modify bandwidth

    myvm.ifaces[0].bandwidth = 10240.0

