# greaseweazle/codec/formats.py
#
# Written & released by Keir Fraser <keir.xen@gmail.com>
#
# This is free and unencumbered software released into the public domain.
# See the file COPYING for more details, or visit <http://unlicense.org>.

import os.path, re
import importlib.resources
from copy import copy

from greaseweazle import error
from greaseweazle.codec.ibm import fm, mfm
from greaseweazle.codec.amiga import amigados
from greaseweazle.tools import util

class Format:

    def __init__(self, disk_config):
        self.fmt = disk_config
        self.default_revs = disk_config.default_revs()
        self.tracks = util.TrackSet(disk_config.trackset())
        self.decode_track = self.fmt.decode_track


class IBMTrackConfig:

    default_revs = mfm.default_revs

    def __init__(self, format_name):
        self.secs = 0
        self.sz = []
        self.id = 1
        self.h = None
        self.format_name = format_name
        self.interleave = 1
        self.cskew, self.hskew = 0, 0
        self.rpm = 300
        self.gap1, self.gap2, self.gap3, self.gap4a = None, None, None, None
        self.iam = True
        self.rate = 0
        self.finalised = False

    def add_param(self, key, val):
        if key == 'secs':
            val = int(val)
            error.check(0 <= val <= 256, '%s out of range' % key)
            self.secs = val
        elif key == 'bps':
            self.sz = []
            for x in val.split(','):
                y = re.match(r'(\d+)\*(\d+)', x)
                if y is not None:
                    n, l = int(y.group(1)), int(y.group(2))
                else:
                    n, l = int(x), 1
                s = 0
                while True:
                    if n == 128<<s:
                        break
                    s += 1
                    error.check(s <= 6, 'bps value out of range')
                for _ in range(l):
                    self.sz.append(s)
        elif key == 'interleave':
            val = int(val)
            error.check(1 <= val <= 255, '%s out of range' % key)
            self.interleave = val
        elif key in ['id', 'cskew', 'hskew']:
            val = int(val)
            error.check(0 <= val <= 255, '%s out of range' % key)
            setattr(self, key, val)
        elif key in ['gap1', 'gap2', 'gap3', 'gap4a', 'h']:
            if val == 'auto':
                val = None
            else:
                val = int(val)
                error.check(0 <= val <= 255, '%s out of range' % key)
            setattr(self, key, val)
        elif key == 'iam':
            error.check(val in ['yes', 'no'], 'bad iam value')
            self.iam = val == 'yes'
        elif key in ['rate', 'rpm']:
            val = int(val)
            error.check(1 <= val <= 2000, '%s out of range' % key)
            setattr(self, key, val)
        else:
            raise error.Fatal('unrecognised track option %s' % key)

    def finalise(self):
        if self.finalised:
            return
        error.check(self.iam or self.gap1 is None,
                    'gap1 specified but no iam')
        error.check(self.secs == 0 or len(self.sz) != 0,
                    'sector size not specified')
        self.finalised = True

    def mk_track(self, cyl, head):
        if self.format_name == 'ibm.mfm':
            t = mfm.IBM_MFM_Config(self, cyl, head)
        else:
            t = fm.IBM_FM_Config(self, cyl, head)
        return t
    

class DiskConfig:

    def __init__(self):
        self.cyls, self.heads = None, None
        self.step = 1
        self.track_map = dict()

    def add_param(self, key, val):
        if key == 'cyls':
            val = int(val)
            error.check(1 <= val <= 255, '%s out of range' % key)
            self.cyls = val
        elif key == 'heads':
            val = int(val)
            error.check(1 <= val <= 2, '%s out of range' % key)
            self.heads = val
        elif key == 'step':
            val = int(val)
            error.check(1 <= val <= 4, '%s out of range' % key)
            self.step = val
        else:
            raise error.Fatal('unrecognised disk option: %s' % key)

    def finalise(self):
        error.check(self.cyls is not None, 'missing cyls')
        error.check(self.heads is not None, 'missing heads')

    def trackset(self):
        s = 'c=0'
        if self.cyls > 1:
            s += '-' + str(self.cyls-1)
        s += ':h=0'
        if self.heads > 1:
            s += '-' + str(self.heads-1)
        if self.step > 1:
            s += ':step=' + str(self.step)
        return s

    def mk_track(self, cyl, head):
        if (cyl, head) not in self.track_map:
            return None
        return self.track_map[cyl, head].mk_track(cyl, head)
    
    def decode_track(self, cyl, head, track):
        t = self.mk_track(cyl, head)
        t.decode_raw(track)
        return t

    def __call__(self, cyl, head):
        return self.mk_track(cyl, head)

    def default_revs(self):
        return max([x.default_revs for x in self.track_map.values()])


class ParseMode:
    Outer = 0
    Disk  = 1
    Track = 2


def get_cfg_lines(cfg):
    if cfg is None:
        cfg = 'diskdefs.cfg'
        with importlib.resources.open_text('greaseweazle.data', cfg) as f:
            lines = f.readlines()
    else:
        with open(os.path.expanduser(cfg), 'r') as f:
            lines = f.readlines()
    return (lines, cfg)


def mk_track_config(format_name):
    if format_name in ['amiga.amigados']:
        return amigados.AmigaDOSTrackConfig(format_name)
    if format_name in ['ibm.mfm','ibm.fm']:
        return IBMTrackConfig(format_name)
    raise error.Fatal('unrecognised format name: %s' % format_name)


def get_format(name, cfg=None):
    parse_mode = ParseMode.Outer
    active, formats = False, []
    disk_config, track_config = None, None
    lines, cfg = get_cfg_lines(cfg)

    for linenr, l in enumerate(lines, start=1):
        try:
            # Strip comments and whitespace.
            t = re.match(r'\s*([^#]*)', l).group(1).strip()

            # Skip empty lines.
            if not t:
                continue

            if parse_mode == ParseMode.Outer:
                disk_match = re.match(r'disk\s+([\w,.-]+)', t)
                error.check(disk_match is not None,
                            'syntax error')
                parse_mode = ParseMode.Disk
                active = disk_match.group(1) == name
                if active:
                    disk_config = DiskConfig()

            elif parse_mode == ParseMode.Disk:
                if t == 'end':
                    parse_mode = ParseMode.Outer
                    active = False
                    continue
                tracks_match = re.match(r'tracks\s+([0-9,.*-]+)'
                                        '\s+([\w,.-]+)', t)
                if tracks_match:
                    parse_mode = ParseMode.Track
                    if not active:
                        continue
                    error.check(disk_config.cyls is not None,
                                'missing cyls')
                    error.check(disk_config.heads is not None,
                                'missing heads')
                    track_config = mk_track_config(tracks_match.group(2))
                    for x in tracks_match.group(1).split(','):
                        if x == '*':
                            for c in range(disk_config.cyls):
                                for hd in range(disk_config.heads):
                                    if (c,hd) not in disk_config.track_map:
                                        disk_config.track_map[c,hd] = track_config
                        else:
                            t_match = re.match(r'(\d+)(?:-(\d+))?'
                                               '(?:\.([01]))?', x)
                            error.check(t_match is not None,
                                        'bad track specifier')
                            s = int(t_match.group(1))
                            e = t_match.group(2)
                            e = s if e is None else int(e)
                            h = t_match.group(3)
                            if h is None:
                                h = list(range(disk_config.heads))
                            else:
                                error.check(int(h) < disk_config.heads,
                                            'head out of range')
                                h = [int(h)]
                            error.check(0 <= s < disk_config.cyls
                                        and 0 <= e < disk_config.cyls
                                        and s <= e,
                                        'cylinder out of range')
                            for c in range(s,e+1):
                                for hd in h:
                                    disk_config.track_map[c,hd] = track_config
                    continue

                if not active:
                    continue

                keyval_match = re.match(r'([a-zA-Z0-9:,._-]+)\s*='
                                        '\s*([a-zA-Z0-9:,._-]+)', t)
                error.check(keyval_match is not None, 'syntax error')
                disk_config.add_param(keyval_match.group(1),
                                      keyval_match.group(2))

            elif parse_mode == ParseMode.Track:
                if t == 'end':
                    parse_mode = ParseMode.Disk
                    if track_config is not None:
                        track_config.finalise()
                        track_config = None
                    continue

                if not active:
                    continue

                keyval_match = re.match(r'([a-zA-Z0-9:,._-]+)\s*='
                                        '\s*([a-zA-Z0-9:,._*-]+)', t)
                error.check(keyval_match is not None, 'syntax error')
                track_config.add_param(keyval_match.group(1),
                                       keyval_match.group(2))

        except Exception as err:
            s = "%s, line %d: " % (cfg, linenr)
            err.args = (s + err.args[0],) + err.args[1:]
            raise

    if disk_config is None:
        return None
    disk_config.finalise()

    return Format(disk_config)


def print_formats(cfg=None):
    formats = []
    lines, _ = get_cfg_lines(cfg)
    for l in lines:
        disk_match = re.match(r'\s*disk\s+([\w,.-]+)', l)
        if disk_match:
            formats.append('  ' + disk_match.group(1))
    formats.sort()
    return '\n'.join(filter(None, formats))
