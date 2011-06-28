'''Base class for store implementations and store-related utility code.'''

import os
import tempfile
import binascii
import bfutil

from mercurial import util, node, error, url as url_, hg
from mercurial.i18n import _

class StoreError(Exception):
    '''Raised when there is a problem getting files from or putting
    files to a central store.'''
    def __init__(self, filename, hash, url, detail):
        self.filename = filename
        self.hash = hash
        self.url = url
        self.detail = detail

    def longmessage(self):
        return ("%s: %s\n"
                "(failed URL: %s)\n"
                % (self.filename, self.detail, self.url))

    def __str__(self):
        return "%s: %s" % (self.url, self.detail)

class basestore(object):
    def __init__(self, ui, repo, url):
        self.ui = ui
        self.repo = repo
        self.url = url

    def put(self, source, hash):
        '''Put source file into the store under <filename>/<hash>.'''
        raise NotImplementedError('abstract method')

    def exists(self, hash):
        '''Check to see if the store contains the given hash.'''
        raise NotImplementedError('abstract method')

    def get(self, files):
        '''Get the specified big files from the store and write to local
        files under repo.root.  files is a list of (filename, hash)
        tuples.  Return (success, missing), lists of files successfuly
        downloaded and those not found in the store.  success is a list
        of (filename, hash) tuples; missing is a list of filenames that
        we could not get.  (The detailed error message will already have
        been presented to the user, so missing is just supplied as a
        summary.)'''
        success = []
        missing = []
        ui = self.ui

        at = 0
        for filename, hash in files:
            ui.progress(_('Getting kbfiles'), at, unit='kbfile', total=len(files))
            at += 1
            ui.note(_('getting %s\n') % filename)
            outfilename = self.repo.wjoin(filename)
            destdir = os.path.dirname(outfilename)
            util.makedirs(destdir)
            if not os.path.isdir(destdir):
                self.abort(error.RepoError(_('cannot create dest directory %s') % destdir))

            # No need to pass mode='wb' to fdopen(), since mkstemp() already
            # opened the file in binary mode.
            (tmpfd, tmpfilename) = tempfile.mkstemp(
                dir=destdir, prefix=os.path.basename(filename))
            tmpfile = os.fdopen(tmpfd, 'w')

            try:
                bhash = self._getfile(tmpfile, filename, hash)
            except StoreError, err:
                ui.warn(err.longmessage())
                os.remove(tmpfilename)
                missing.append(filename)
                continue

            hhash = binascii.hexlify(bhash)
            if hhash != hash:
                ui.warn(_('%s: data corruption (expected %s, got %s)\n')
                        % (filename, hash, hhash))
                os.remove(tmpfilename)
                missing.append(filename)
            else:
                if os.path.exists(outfilename):          # for windows
                    os.remove(outfilename)
                os.rename(tmpfilename, outfilename)
                bfutil.copy_to_cache(self.repo, self.repo['.'].node(), filename, True)
                success.append((filename, hhash))

        ui.progress(_('Getting bfiles'), None)
        return (success, missing)

    def verify(self, revs, contents=False):
        '''Verify the existence (and, optionally, contents) of every big
        file revision referenced by every changeset in revs.
        Return 0 if all is well, non-zero on any errors.'''
        write = self.ui.write
        failed = False

        write(_('searching %d changesets for big files\n') % len(revs))
        verified = set()                # set of (filename, filenode) tuples

        for rev in revs:
            cctx = self.repo[rev]
            cset = "%d:%s" % (cctx.rev(), node.short(cctx.node()))

            for standin in cctx:
                failed = (self._verifyfile(cctx,
                                           cset,
                                           contents,
                                           standin,
                                           verified)
                          or failed)

        num_revs = len(verified)
        num_bfiles = len(set([fname for (fname, fnode) in verified]))
        if contents:
            write(_('verified contents of %d revisions of %d big files\n')
                  % (num_revs, num_bfiles))
        else:
            write(_('verified existence of %d revisions of %d big files\n')
                  % (num_revs, num_bfiles))

        return int(failed)

    def _getfile(self, tmpfile, filename, hash):
        '''Fetch one revision of one file from the store and write it
        to tmpfile.  Compute the hash of the file on-the-fly as it
        downloads and return the binary hash.  Close tmpfile.  Raise
        StoreError if unable to download the file (e.g. it does not
        exist in the store).'''
        raise NotImplementedError('abstract method')

    def _verifyfile(self, cctx, cset, contents, standin, verified):
        '''Perform the actual verification of a file in the store.
        '''
        raise NotImplementedError('abstract method')

import localstore, httpstore

_store_provider = {
    'file':  (localstore, 'localstore'),
    'http':  (httpstore, 'httpstore'),
    'https': (httpstore, 'httpstore'),
    }

# During clone this function is passed the src's ui object
# but it needs the dest's ui object so it can read out of
# the config file. Use repo.ui instead.
def _open_store(repo, path=None, put=False):
    ui = repo.ui
    if not path:
        path = ui.expandpath('default-push', 'default')
        # If 'default-push' and 'default' can't be expanded
        # they are just returned. In that case use the empty string which
        # use the filescheme.
        if path is 'default-push' or path is 'default':
            path = ''

    # The path could be a scheme so use Mercurial's normal functionality
    # to resolve the scheme to a repository and use its path
    if path:
        path = hg.repository(ui, path).path

    match = url_.scheme_re.match(path)
    if not match:                       # regular filesystem path
        scheme = 'file'
    else:
        scheme = match.group(1)

    try:
        (mod, klass) = _store_provider[scheme]
    except KeyError:
        raise util.Abort(_('unsupported URL scheme %r') % scheme)

    klass = getattr(mod, klass)
    return klass(ui, repo, path)
