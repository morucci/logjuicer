# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import gzip
import lzma
import os
import re
import logging
try:
    from systemd import journal
    import datetime
    import time
    journal_installed = True
except ImportError:
    journal_installed = False


# Avoid those files that aren't useful for words analysis
DEFAULT_IGNORE_PATHS = [
    "zuul-info/",
    '_zuul_ansible/',
    'ara-report/',
    'ara-sf/',
    'ara/',
    'etc/hostname',
    'etc/nodepool/provider',
    # sf-ci useless static files
    "executor.*/trusted/",
    # tripleo-ci static files
    "etc/selinux/targeted/",
    "etc/sysconfig/",
    "etc/systemd/",
    "etc/polkit-1/",
    "etc/pki/",
    "group_vars/all.yaml",
    "keystone/credential-keys",
    "keystone/fernet-keys",
    # extra/logstash is already printed in deploy logs
    "extra/logstash.txt",
    "migration/identity.gz",
    "swift/backups/",
    "/conf.modules.d/",
    "/lib/heat-config/heat-config-script/",
    "\.git/",
    "\.svn/",
]

DEFAULT_IGNORE_FILES = [
    'btmp.txt',
    'cpuinfo.txt',
    'devstack-gate-setup-host.txt',
    'df.txt',
    'dstat.txt',
    'free.txt',
    'heat-deploy-times.log.txt',
    'host_info.txt',
    'hosts.txt',
    'id_rsa',
    'index.html',
    'iostat.txt',
    'iotop.txt',
    'lastlog',
    'last',
    'authkey',
    'lsmod.txt',
    'lsof.txt',
    'lsof_network.txt',
    'meminfo.txt',
    'nose_results.html',
    'passwords.yml',
    'postci.txt',
    'pstree.txt',
    'ps.txt',
    'rdo-trunk-deps-end.txt',
    'repolist.txt',
    'service_configs.json.txt',
    'sysctl.txt',
    'sysstat.txt',
    'tempest.log.txt',
    'tempest_output.log.txt',
    'uname.txt',
    'worlddump-',
    'wtmp.txt',
    'README',
    'unbound.log',
    'dns_cache.txt',
    'password.gz',
    'moduli',
    'screen-dstat',
]

BLACKLIST_EXTENSIONS = (
    ".sqlite",
    ".svg",
    ".woff",
    ".ttf",
    ".css",
    ".js",
    ".db",
    ".ico",
    ".png",
    ".tgz",
    ".pyc",
    ".pyo",
    ".so",
    ".key",
    "_key",
    ".crt",
    ".csr",
    ".pem",
    ".rpm",
    ".subunit",
    ".journal",
    ".json",
    ".json.txt",
    ".yaml.txt",
    ".conf",
    ".conf.txt",
    ".yaml",
    ".yml",
    "ring.gz",
)

FACILITY2NAME = {
    0: 'kern',
    1: "user",
    2: "mail",
    3: "daemon",
    4: "auth",
    5: "syslog",
    6: "lprlog",
    7: "news",
    8: "uucp",
    9: "clock",
    10: "authpriv",
    11: "ftplog",
    12: "unknown",
    13: "unknown",
    14: "unknown",
    15: "cron",
    16: "local0",
    17: "local1",
    18: "local2",
    19: "local3",
    20: "local4",
    21: "local5",
    22: "local6",
    23: "local7",
}


class Journal:
    def __init__(self, since, previous=False):
        if not journal_installed:
            raise RuntimeError(
                "Please run dnf install -y python3-systemd to continue")
        _day = 3600 * 24
        if since.lower() == "day":
            ts = _day
        elif since.lower() == "week":
            ts = 7 * _day
        elif since.lower() == "month":
            ts = 30 * _day
        else:
            raise RuntimeError("%s: Unknown since timestamp" % since)
        if previous:
            self.name = "last %s" % since
            self.since = time.time() - ts * 2
            self.until = self.since + ts
        else:
            self.name = "this %s" % since
            self.since = time.time() - ts
            self.until = None

    def open(self):
        self.journal = journal.Reader()
        self.journal.seek_realtime(self.since)

    def close(self):
        self.journal.close()
        del self.journal

    def readline(self):
        entry = self.journal.get_next()
        ts = entry.get('__REALTIME_TIMESTAMP', datetime.datetime(1970, 1, 1))
        if not entry or (self.until and ts.timestamp() > self.until):
            return b''
        facility = entry.get('SYSLOG_FACILITY')
        if isinstance(facility, int):
            entry['LEVEL'] = FACILITY2NAME.get(facility, 'NOTI').upper()
        else:
            entry['LEVEL'] = str(facility)
        entry['DATE'] = ts.strftime('%Y-%m-%d %H:%M:%S')
        entry.setdefault("SYSLOG_IDENTIFIER", "NONE")
        entry.setdefault("MESSAGE", "NONE")
        return "{DATE} - {SYSLOG_IDENTIFIER} - {LEVEL} - {MESSAGE}\n".format(
            **entry).encode('utf-8')

    def __str__(self):
        return "Journal of %s" % self.name


def open_file(p):
    if isinstance(p, Journal):
        p.open()
        return p
    if p.endswith(".gz"):
        # check if really gzip, logs.openstack.org return decompressed files
        if open(p, 'rb').read(2) == b'\x1f\x8b':
            return gzip.open(p, mode='r')
    elif p.endswith(".xz"):
        return lzma.open(p, mode='r')
    return open(p, 'rb')


def files_iterator(paths, ign_files=[], ign_paths=[]):
    """Walk directory and yield (path, rel_path)"""
    if not isinstance(paths, list):
        paths = [paths]
    else:
        # Copy path list
        paths = list(paths)
    for path in paths:
        if isinstance(path, Journal):
            yield (path, "")
        elif os.path.isfile(path):
            yield (path, os.path.basename(path))
        elif os.path.isdir(path):
            if path[-1] != "/":
                path = "%s/" % path
            for dname, _, fnames in os.walk(path):
                for fname in fnames:
                    if [True for ign in ign_files if re.match(ign, fname)]:
                        continue
                    if [True for skip in BLACKLIST_EXTENSIONS if
                            fname.endswith("%s" % skip) or
                            fname.endswith("%s.gz" % skip) or
                            fname.endswith("%s.txt.gz" % skip) or
                            fname.endswith("%s.bz2" % skip) or
                            fname.endswith("%s.xz" % skip)]:
                        continue
                    fpath = os.path.join(dname, fname)

                    # Skip empty files
                    try:
                        zero_sizes = [0]
                        if ".gz" in fpath:
                            zero_sizes.append(20)
                        if os.stat(fpath).st_size in zero_sizes:
                            continue
                    except Exception:
                        pass

                    rel_path = fpath[len(path):]
                    if [True for ign in ign_paths if re.search(ign, rel_path)]:
                        continue
                    yield (fpath, rel_path)
        else:
            raise RuntimeError("%s: unknown uri" % path)


def setup_logging(debug=False):
    loglevel = logging.INFO
    if debug:
        loglevel = logging.DEBUG
    logging.basicConfig(
        format='%(asctime)s %(levelname)-5.5s %(name)s - %(message)s',
        level=loglevel)


def format_speed(count, size, elapsed_time):
    """Return speed in MB/s and kilo-line count/s"""
    return "%.03fs at %.03fMB/s (%0.3fkl/s) (%.03f MB - %.03f kilo-lines)" % (
        elapsed_time,
        (size / (1024 * 1024)) / elapsed_time,
        (count / 1000) / elapsed_time,
        (size / (1024 * 1024)),
        (count / 1000),
    )
