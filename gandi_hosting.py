# -*- coding: utf-8 -*-
"""
Gandi Hosting XML/RPC API wrapper

Copyright (c) 2010 Evax Software <contact@evax.fr>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""

import time
import datetime
import xmlrpclib

from ConfigParser import ConfigParser

hosting_uri = 'https://rpc.gandi.net/xmlrpc/2.0/'

class ObjectContainer(list):
    """A list allowing to get an object by matching a search field."""
    def __init__(self, gh=None, cls=None):
        list.__init__(self)
        self._gh = gh
        self._class = cls
        self._obj_name = cls and cls.__name__.lower() or None
        self.refresh()

    def __getitem__(self, key):
        if isinstance(key, int) or isinstance(key, slice):
            return list.__getitem__(self, key)
        for i in self:
            for attr in i._searchable_attrs:
                if getattr(i, attr, None) == key:
                    return i
        return None

    def refresh(self):
        if self._gh is None or self._class is None:
            return
        del self[:]
        for o in self._gh.call(self._obj_name+'.list'):
            self.append(self._class(self._gh, o))

class SimpleMapper(object):
    """A lightweight Object Mapper on top of the XML/RPC API."""
    def __init__(self, gandi_hosting, spec):
        self._obj_name = self.__class__.__name__.lower()
        self._gh = gandi_hosting
        self._build_from_spec(spec)
        self._post_init()

    _searchable_attrs = ()

    @classmethod
    def from_id(cls, gh, id):
        """Query an object from id and return a wrapper."""
        return cls(gh, gh.call(cls.__name__.lower()+'.info', id))

    def _post_init(self):
        """Called after __init__."""
        pass

    def _build_from_spec(self, spec):
        self._spec = spec
        for k, v in spec.items():
            if k.endswith('_id'):
                setattr(self, k, v)
                self._register_backref(k)
            else:
                setattr(self, '_'+k, v)
                self._register_property(k)

    def _register_property(self, k):
        fget = lambda self: getattr(self, '_'+k)
        fset = None
        updatable_attrs = getattr(self, '_updatable_attrs', ())
        if k in updatable_attrs:
            fset = lambda self, value: self._set(k, value)
        setattr(self.__class__, k, property(fget=fget, fset=fset))

    def refresh(self):
        raw = self._gh.call(self.__class__.__name__.lower()+'.info',
                            self.id)
        if isinstance(raw, list):
            raw = raw[0]
        self._build_from_spec(raw)

    def _set(self, name, value):
        spec = { name: value }
        if self.update(spec) == 'DONE':
            setattr(self, '_'+name, value)

    def _get_single_backref(self, name):
        if getattr(self, '_'+name, None) is None:
            setattr(self, '_'+name,
                    self._gh._get_gandi_object_by_id(name,
                                                     getattr(self,
                                                             name+'_id')))
        return getattr(self, '_'+name)

    def _get_multi_backref(self, name):
        if getattr(self, '_'+name, None) is None:
            objs = ObjectContainer()
            for i in getattr(self, name+'_id', []):
                objs.append(self._gh._get_gandi_object_by_id(name[:-1], i))
            if len(objs) > 0:
                setattr(self, '_'+name, objs)
        return getattr(self, '_'+name)

    def _register_backref(self, ref_id):
        name = ref_id[:-3]
        fget = None
        if name in self._gh._gandi_objects:
            fget = lambda self: self._get_single_backref(name)
        elif name[:-1] in self._gh._gandi_objects:
            fget = lambda self: self._get_multi_backref(name)
        else:
            # silently ignore
            return
        setattr(self.__class__, name, property(fget=fget))

class ExtendedMapper(SimpleMapper):
    """Extended mapper for objects that can be created/updated/deleted."""
    _create_specs = {}
    @classmethod
    def create(cls, gh, **kwargs):
        """Create an object and return the wrapper."""
        obj_name = cls.__name__.lower()
        for k, v in cls._create_specs.items():
            if v == 'mandatory' and k not in kwargs.keys():
                raise KeyError('Missing mandatory key: %s' % k)
        specs = {}
        for k, v in kwargs.items():
            if k in cls._create_specs.keys():
                specs[k] = v
        op = Operation(gh, gh.call(obj_name+'.create', specs))
        if op.wait_completion() == 'DONE':
            obj = cls.from_id(gh, getattr(op, obj_name+'_id'))
            gh.container(obj_name).refresh()
            return obj

    _updatable_attrs = ()
    def update(self, specs):
        for k in specs.keys():
            if k not in self._updatable_attrs:
                raise KeyError('Attribute %k is not updatable' % k)
        op = Operation(self._gh,
                       self._gh.call(self._obj_name+'.update',
                                     self.id, specs))
        return op.wait_completion()

    def delete(self):
        delete_hook = getattr(self, '_delete_hook', None)
        delete_hook and delete_hook()
        op = Operation(self._gh,
                       self._gh.call(self._obj_name+'.delete', self.id))
        ret = op.wait_completion()
        ret == 'DONE' and self._gh.container(self._obj_name).refresh()
        return ret

class Datacenter(SimpleMapper):
    _searchable_attrs = ('name', 'country', 'iso')

    def __repr__(self):
        return '<Datacenter %d: %s, %s (%s)>' \
                    % (self.id, self.name, self.country, self.iso)

class Image(SimpleMapper):
    _searchable_attrs = ('label', 'name')

    def __repr__(self):
        return '<Image %d: %s (%s) - %s - %s>' \
                % (self.id, self.label, self.os_arch,
                   self.datacenter.iso, self.visibility)

    def copy_as(self, name, repulse_from=None):
        specs = { 'datacenter_id': self.datacenter.id,
                  'name': name }
        if repulse_from:
            specs[repulse_from] = repulse_from
        op = Operation(self._gh,
                       self._gh.call('disk.create_from', specs,
                                     self.disk_id))
        if op.wait_completion() == 'DONE':
            disk = Disk.from_id(self._gh, op.disk_id)
            self._gh.disks.append(disk)
            return disk
        return None

class Vm(ExtendedMapper):
    _states = ('paused', 'running', 'halted', 'locked', 'being_created',
              'invalid', 'legally_locked', 'deleted')

    _updatable_attrs = ('vm_max_memory', 'shares', 'memory', 'console',
                        'password')
    _searchable_attrs = ('hostname',)

    def __repr__(self):
        return '<VM %d: %s (%s) - %s%s>' \
                % (self.id, self.hostname, self.datacenter.iso,
                   self.state, self.console and ' - console on' or '')

    def _post_init(self):
        for action in ('stop', 'start', 'reboot'):
            self._setup_action(action)

    def _setup_action(self, action):
        f = lambda self: self._op('vm.'+action)
        setattr(self.__class__, action, f)

    def is_valid(self):
        return self.state in ('being_created', 'halted', 'running')

    def _delete_hook(self):
        self.refresh()
        if self.state == 'running':
            self._op('vm.stop')

    def _op(self, op, refresh=True):
        op = Operation(self._gh, self._gh.call(op, self.id))
        ret = op.wait_completion()
        if refresh:
            self.refresh()
        return ret

class Disk(ExtendedMapper):
    _updatable_attrs = ('name', 'size', 'kernel', 'cmdline_option',
                       'cmdline')
    _searchable_attrs = ('name',)
    _create_specs = { 'datacenter_id': 'mandatory',
                      'name': 'mandatory',
                      'size': 'mandatory',
                      'type': 'mandatory',
                      'repulse_from': 'optional' }

    def __repr__(self):
        return '<Disk %d: %s - %s>' % (self.id, self.name, self.label)

class Iface(ExtendedMapper):
    _updatable_attrs = ( 'bandwidth' )
    _create_specs = { 'datacenter_id': 'mandatory',
                     'ip_version': 'mandatory',
                     'bandwidth': 'optional' }

    def __repr__(self):
        return '<Iface %d: %d - %s - %s>' \
                % (self.id, self.bandwidth, self.type, self.state)

class Ip(ExtendedMapper):
    _updatable_attrs = ( 'reverse' )
    _searchable_attrs = ('ip', 'reverse')
    _create_specs = { 'datacenter_id': 'mandatory',
                     'ip_version': 'mandatory',
                     'reverse': 'optional' }

    def __repr__(self):
        return '<Ip %d: %s (%s) - %s>' \
                % (self.id, self.ip, self.reverse, self.state)

class Operation(SimpleMapper):
    def __init__(self, gh, rdef):
        SimpleMapper.__init__(self, gh, rdef)
        self._gh.operations.append(self)

    def __repr__(self):
        return '<Operation %d: %s - %s>' % (self.id, self.type, self.step)

    def wait_completion(self, timeout=None, sleep=1):
        left = 1
        if timeout:
            left = timeout
        while self.step not in ('DONE', 'ERROR') and left > 0:
            time.sleep(sleep)
            if timeout:
                left -= sleep
            self.refresh()
        return self.step

class Account(object):
    def __init__(self, gandi_hosting):
        self._gh = gandi_hosting
        for prop in ('fullname', 'handle', 'id'):
            self._setup_prop(prop)

    def __repr__(self):
        return '<Account %d: %s (%s)>' \
                % (self.id, self.handle, self.fullname)

    def _setup_prop(self, prop):
        fget = lambda self: self._get_prop(prop)
        setattr(self.__class__, prop, property(fget=fget))

    def _get_prop(self, prop):
        return self.info[prop]

    def refresh(self):
        self._info = self._products = self._resources = None

    @property
    def info(self):
        if not getattr(self, '_info', None):
            self._info = self._gh.call('account.info')
        return self._info

    @property
    def products(self):
        if not getattr(self, '_products', None):
            prods = []
            for p in self.info['products']:
                prods.append(Product(self._gh, p))
            if len(prods) > 0:
                self._products = prods
        return self._products

    @property
    def resources(self):
        if not getattr(self, '_resources', None):
            res = {}
            for t, r in self.info['resources'].items():
                res[t] = Resource(self._gh, t,r)
            if len(res) > 0:
                self._resources = res
        return self._resources

class Product(SimpleMapper):
    def __repr__(self):
        date_end = datetime.datetime.strptime(self.date_end.value,
                                              "%Y%m%dT%H:%M:%S")
        return '<Product %d: %d %s(s) - expiration: %s>' \
                    % (self.id, self.quantity, self.product_name,
                       date_end.strftime('%d/%m/%Y'))

class Resource(SimpleMapper):
    def __init__(self, gh, type, rdef):
        self.type = type
        SimpleMapper.__init__(self, gh, rdef)

    def __repr__(self):
        r = '<%s resources: ' % self.type
        vals = []
        for a in ('bandwidth: %d', 'cores: %f', 'disk: %d', 'ips: %d',
                  'memory: %d', 'servers: %d', 'shares: %s', 'slots: %f'):
            attr = a.split(':')[0]
            if hasattr(self, attr):
                vals.append(a%getattr(self, attr))
        return r+' | '.join(vals)+' >'

class GandiHosting(object):
    """Handles XML/RPC communications and cache results."""
    _mapped_classes = (Datacenter, Image, Vm, Disk, Iface, Ip)
    _gandi_objects = [ mc.__name__.lower() for mc in _mapped_classes ]
    def __init__(self, key, uri=hosting_uri, config=None):
        self.key, self.uri, self.config = key, uri, config
        self.api = xmlrpclib.ServerProxy(self.uri)
        for cls in self._mapped_classes:
            self._register_gandi_class(cls)

        self.operations = []
        self.account = Account(self)

    @classmethod
    def from_config(cls, config_path):
        config = ConfigParser()
        config.read(config_path)
        uri = hosting_uri
        if config.has_option('hosting', 'uri'):
            uri = config.get('hosting', 'uri')
        key = None
        if config.has_option('hosting', 'key'):
            key = config.get('hosting', 'key')
        if key:
            return GandiHosting(key, uri, config)
        return None

    def get_option(self, section, option, typename=''):
        if self.config is None:
            return None
        if not self.config.has_option(section, option):
            return None
        return getattr(self.config, 'get'+typename)(section, option)

    def container(self, obj_name):
        return getattr(self, obj_name+'s', None)

    def call(self, method_name, *args):
        """Calls the API handling credentials."""
        return getattr(self.api, method_name)(self.key, *args)

    def _register_gandi_class(self, cls):
        obj_name = cls.__name__.lower()
        fget = lambda self: self._get_gandi_objects(cls)
        by_id = lambda self, id: \
                        self._get_gandi_object_by_id(obj_name, id)
        setattr(self.__class__, obj_name+'s', property(fget=fget))
        setattr(self.__class__, 'get_'+obj_name+'_by_id', by_id)

    def _get_gandi_object_by_id(self, go, id):
        for o in getattr(self, go+'s', []):
            if o.id == id:
                return o
        return None

    def _get_gandi_objects(self, cls):
        container_name = '_'+cls.__name__.lower()+'s'
        container = getattr(self, container_name, None)
        if container is None:
            container = ObjectContainer(self, cls)
            setattr(self, container_name, container)
        return container

