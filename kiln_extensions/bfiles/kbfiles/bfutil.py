'''bfiles utility code: must not import other modules in this package.'''

import os
import errno
import inspect
import shutil
import stat

from mercurial import \
    util, dirstate, cmdutil, match as match_
from mercurial.i18n import _

short_name = '.kbf'
long_name = 'kilnbfiles'


# -- Portability wrappers ----------------------------------------------

if 'subrepos' in inspect.getargspec(dirstate.dirstate.status)[0]:
    # for Mercurial >= 1.5
    def dirstate_walk(dirstate, matcher, unknown=False, ignored=False):
        return dirstate.walk(matcher, [], unknown, ignored)
else:
    # for Mercurial <= 1.4
    def dirstate_walk(dirstate, matcher, unknown=False, ignored=False):
        return dirstate.walk(matcher, unknown, ignored)

def repo_add(repo, list):
    try:
        # Mercurial <= 1.5
        add = repo.add
    except AttributeError:
        # Mercurial >= 1.6
        add = repo[None].add
    return add(list)

def repo_remove(repo, list, unlink=False):
    try:
        # Mercurial <= 1.5
        remove = repo.remove
    except AttributeError:
        # Mercurial >= 1.6
        remove = repo[None].remove
    return remove(list, unlink=unlink)

def repo_forget(repo, list):
    try:
        # Mercurial <= 1.5
        forget = repo.forget
    except AttributeError:
        # Mercurial >= 1.6
        forget = repo[None].forget
    return forget(list)

def dirstate_normaldirty(dirstate, file):
    try:
        normaldirty = dirstate.normaldirty
    except AttributeError:
        # Mercurial >= 1.6: HAAAACK: I should not be using normaldirty()
        # (now called otherparent()), and dirstate in 1.6 prevents me
        # from doing so.  So reimplement it here until I figure out the
        # right fix.
        def normaldirty(f):
            dirstate._dirty = True
            dirstate._addpath(f)
            dirstate._map[f] = ('n', 0, -2, -1)
            if f in dirstate._copymap:
                del dirstate._copymap[f]
    normaldirty(file)

def findoutgoing(repo, remote, force):
    # First attempt is for Mercurial <= 1.5 second is for >= 1.6
    try:
        return repo.findoutgoing(remote)
    except AttributeError:
        from mercurial import discovery
        return discovery.findoutgoing(repo, remote, force=force)

# -- Private worker functions ------------------------------------------

if os.name == 'nt':
    from mercurial import win32
    linkfn = win32.os_link
else:
    linkfn = os.link

def link(src, dest):
    try:
        linkfn(src, dest)
    except OSError:
        # If hardlinks fail fall back on copy
        shutil.copyfile(src, dest)
        os.chmod(dest, os.stat(src).st_mode)

def system_cache_path(ui, hash):
    path = ui.config(long_name, 'systemcache', None)
    if path:
        path = os.path.join(path, hash)
    else:
        if os.name == 'nt':
            path = os.path.join(os.getenv('LOCALAPPDATA') or os.getenv('APPDATA'), long_name, hash)
        elif os.name == 'posix':
            path = os.path.join(os.getenv('HOME'), '.' + long_name, hash)
        else:
            raise util.Abort(_('Unknown operating system: %s\n') % os.name)
    return path

def in_system_cache(ui, hash):
    return os.path.exists(system_cache_path(ui, hash))

def find_file(repo, hash):
    if in_cache(repo, hash):
        repo.ui.note(_('Found %s in cache\n') % hash)
        return cache_path(repo, hash)
    if in_system_cache(repo.ui, hash):
        repo.ui.note(_('Found %s in system cache\n') % hash)
        return system_cache_path(repo.ui, hash)
    return None

def open_bfdirstate(ui, repo):
    '''
    Return a dirstate object that tracks big files: i.e. its root is the
    repo root, but it is saved in .hg/bfiles/dirstate.
    '''
    admin = repo.join(long_name)
    opener = util.opener(admin)
    if hasattr(repo.dirstate, '_validate'):
        bfdirstate = dirstate.dirstate(opener, ui, repo.root, repo.dirstate._validate)
    else:
        bfdirstate = dirstate.dirstate(opener, ui, repo.root)

    # If the bfiles dirstate does not exist, populate and create it.  This
    # ensures that we create it on the first meaningful bfiles operation in
    # a new clone.  It also gives us an easy way to forcibly rebuild bfiles
    # state:
    #   rm .hg/bfiles/dirstate && hg bfstatus
    # Or even, if things are really messed up:
    #   rm -rf .hg/bfiles && hg bfstatus
    # (although that can lose data, e.g. pending big file revisions in
    # .hg/bfiles/{pending,committed}).
    if not os.path.exists(os.path.join(admin, 'dirstate')):
        util.makedirs(admin)
        matcher = get_standin_matcher(repo)
        for standin in dirstate_walk(repo.dirstate, matcher):
            bigfile = split_standin(standin)
            hash = read_standin(repo, standin)
            try:
                curhash = hashfile(bigfile)
            except IOError, err:
                if err.errno == errno.ENOENT:
                    dirstate_normaldirty(bfdirstate, bigfile)
                else:
                    raise
            else:
                if curhash == hash:
                    bfdirstate.normal(unixpath(bigfile))
                else:
                    dirstate_normaldirty(bfdirstate, bigfile)

        bfdirstate.write()

    return bfdirstate

def bfdirstate_status(bfdirstate, repo, rev):
    wlock = repo.wlock()
    try:
        match = match_.always(repo.root, repo.getcwd())
        s = bfdirstate.status(match, [], False, False, False)
        (unsure, modified, added, removed, missing, unknown, ignored, clean) = s
        for bfile in unsure:
            if repo[rev][standin(bfile)].data().strip() != hashfile(repo.wjoin(bfile)):
                modified.append(bfile)
            else:
                clean.append(bfile)
                bfdirstate.normal(unixpath(bfile))
        bfdirstate.write()
    finally:
        wlock.release()
    return (modified, added, removed, missing, unknown, ignored, clean)

def list_bfiles(repo, rev=None, matcher=None):
    '''list big files in the working copy or specified changeset'''

    if matcher is None:
        matcher = get_standin_matcher(repo)

    bfiles = []
    if rev:
        cctx = repo[rev]
        for standin in cctx.walk(matcher):
            filename = split_standin(standin)
            bfiles.append(filename)
    else:
        for standin in sorted(dirstate_walk(repo.dirstate, matcher)):
            filename = split_standin(standin)
            bfiles.append(filename)
    return bfiles

def in_cache(repo, hash):
    return os.path.exists(cache_path(repo, hash))

def create_dir(dir):
    if not os.path.exists(dir):
        os.makedirs(dir)

def cache_path(repo, hash):
    return repo.join(os.path.join(long_name, hash))

def copy_to_cache(repo, rev, file, uploaded=False):
    hash = read_standin(repo, standin(file))
    if in_cache(repo, hash):
        return
    create_dir(os.path.dirname(cache_path(repo, hash)))
    if in_system_cache(repo.ui, hash):
        link(system_cache_path(repo.ui, hash), cache_path(repo, hash))
    else:
        shutil.copyfile(repo.wjoin(file), cache_path(repo, hash))
        os.chmod(cache_path(repo, hash), os.stat(repo.wjoin(file)).st_mode)
        create_dir(os.path.dirname(system_cache_path(repo.ui, hash)))
        link(cache_path(repo, hash), system_cache_path(repo.ui, hash))

def get_standin_matcher(repo, pats=[], opts={}):
    '''Return a match object that applies pats to <repo>/.kbf.'''
    standin_dir = repo.pathto(short_name)
    if pats:
        # patterns supplied: search .hgbfiles relative to current dir
        cwd = repo.getcwd()
        pats = [os.path.join(standin_dir, cwd, pat) for pat in pats]
    elif os.path.isdir(standin_dir):
        # no patterns: relative to repo root
        pats = [standin_dir]
    else:
        # no patterns and no .hgbfiles dir: return matcher that matches nothing
        match = match_.match(repo.root, None, [], exact=True)
        match.matchfn = lambda f: False
        return match
    return get_matcher(repo, pats, opts, showbad=False)

def get_matcher(repo, pats=[], opts={}, showbad=True):
    '''Wrapper around cmdutil.match() that adds showbad: if false, neuter
    the match object\'s bad() method so it does not print any warnings
    about missing files or directories.'''
    match = cmdutil.match(repo, pats, opts)
    if not showbad:
        match.bad = lambda f, msg: None
    return match

def compose_standin_matcher(repo, rmatcher):
    '''Return a matcher that accepts standins corresponding to the files
    accepted by rmatcher. Pass the list of files in the matcher as the
    paths specified by the user.'''
    smatcher = get_standin_matcher(repo, rmatcher.files())
    isstandin = smatcher.matchfn
    def composed_matchfn(f):
        return isstandin(f) and rmatcher.matchfn(split_standin(f))
    smatcher.matchfn = composed_matchfn

    return smatcher

def standin(filename):
    '''Return the repo-relative path to the standin for the specified big
    file.'''
    # Notes:
    # 1) Most callers want an absolute path, but _create_standin() needs
    #    it repo-relative so bfadd() can pass it to repo_add().  So leave
    #    it up to the caller to use repo.wjoin() to get an absolute path.
    # 2) Join with '/' because that's what dirstate always uses, even on
    #    Windows. Change existing separator to '/' first in case we are
    #    passed filenames from an external source (like the command line).
    return short_name + '/' + filename.replace(os.sep, '/')

def is_standin(filename):
    '''Return true if filename is a big file standin.  filename must
    be in Mercurial\'s internal form (slash-separated).'''
    return filename.startswith(short_name+'/')

def split_standin(filename):
    # Split on / because that's what dirstate always uses, even on Windows.
    # Change local separator to / first just in case we are passed filenames
    # from an external source (like the command line).
    bits = filename.replace(os.sep, '/').split('/', 1)
    if len(bits) == 2 and bits[0] == short_name:
        return bits[1]
    else:
        return None

def update_standin(repo, standin):
    file = repo.wjoin(split_standin(standin))
    hash = hashfile(file)
    executable = get_executable(file)
    write_standin(repo, standin, hash, executable)

def read_standin(repo, standin):
    '''read hex hash from <repo.root>/<standin>'''
    return read_hash(repo.wjoin(standin))

def write_standin(repo, standin, hash, executable):
    '''write hhash to <repo.root>/<standin>'''
    write_hash(hash, repo.wjoin(standin), executable)

def copy_and_hash(instream, outfile):
    '''Read bytes from instream (iterable) and write them to outfile,
    computing the SHA-1 hash of the data along the way.  Close outfile
    when done and return the binary hash.'''
    hasher = util.sha1('')
    for data in instream:
        hasher.update(data)
        outfile.write(data)

    # Blecch: closing a file that somebody else opened is rude and
    # wrong.  But it's so darn convenient and practical!  After all,
    # outfile was opened just to copy and hash.
    outfile.close()

    return hasher.digest()

def hashrepofile(repo, file):
    return hashfile(repo.wjoin(file))

def hashfile(file):
    hasher = util.sha1('')
    with open(file, 'rb') as fd:
        for data in blockstream(fd):
            hasher.update(data)
    return hasher.hexdigest()

def blockstream(infile, blocksize=128*1024):
    """Generator that yields blocks of data from infile and closes infile."""
    while True:
        data = infile.read(blocksize)
        if not data:
            break
        yield data
    # Same blecch as above.
    infile.close()

def read_hash(filename):
    rfile = open(filename, 'rb')
    hash = rfile.read(40)
    rfile.close()
    if len(hash) < 40:
        raise util.Abort(_('bad hash in \'%s\' (only %d bytes long)')
                         % (filename, len(hash)))
    return hash

def write_hash(hash, filename, executable):
    util.makedirs(os.path.dirname(filename))
    if os.path.exists(filename):
        os.unlink(filename)
    if os.name == 'posix':
        # Yuck: on Unix, go through open(2) to ensure that the caller's mode is
        # filtered by umask() in the kernel, where it's supposed to be done.
        wfile = os.fdopen(os.open(filename, os.O_WRONLY|os.O_CREAT, get_mode(executable)), 'wb')
    else:
        # But on Windows, use open() directly, since passing mode='wb' to os.fdopen()
        # does not work.  (Python bug?)
        wfile = open(filename, 'wb')

    try:
        wfile.write(hash)
        wfile.write('\n')
    finally:
        wfile.close()

def get_executable(filename):
    mode = os.stat(filename).st_mode
    return (mode & stat.S_IXUSR) and (mode & stat.S_IXGRP) and (mode & stat.S_IXOTH)

def get_mode(executable):
    if executable:
        return 0755
    else:
        return 0644

def urljoin(first, second, *arg):
    def join(left, right):
        if not left.endswith('/'):
            left += '/'
        if right.startswith('/'):
            right = right[1:]
        return left + right

    url = join(first, second)
    for a in arg:
        url = join(url, a)
    return url

# Convert a path to a unix style path. This is used to give a
# canonical path to the bfdirstate.
def unixpath(path):
    return os.path.normpath(path).replace(os.sep, '/')
