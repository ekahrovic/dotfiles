'''Store class for local filesystem.'''

import os

from mercurial import util
from mercurial.i18n import _
import bfutil, basestore

class localstore(basestore.basestore):
    '''Because there is a system wide cache, the local store always uses that cache.
       Since the cache is updated elsewhere, we can just read from it here as if it were the store.'''

    def __init__(self, ui, repo, url):
        url = os.path.join(url, '.hg', bfutil.long_name)
        super(localstore, self).__init__(ui, repo, util.expandpath(url))

    def put(self, source, filename, hash):
        '''Any file that is put must already be in the system wide cache so do nothing.'''
        return

    def exists(self, hash):
        return bfutil.in_system_cache(self.repo.ui, hash)

    def _getfile(self, tmpfile, filename, hash):
        if bfutil.in_system_cache(self.ui, hash):
            return bfutil.system_cache_path(self.ui, hash)
        raise basestore.StoreError(filename, hash, '', _("Can't get file locally"))

    def _verifyfile(self, cctx, cset, contents, standin, verified):
        filename = bfutil.split_standin(standin)
        if not filename:
            return False
        fctx = cctx[standin]
        key = (filename, fctx.filenode())
        if key in verified:
            return False

        expect_hash = fctx.data()[0:40]
        verified.add(key)
        if not bfutil.in_system_cache(self.ui, expect_hash):
            self.ui.warn(
                _('changeset %s: %s missing\n'
                  '  (%s: %s)\n')
                % (cset, filename, expect_hash, err.strerror))
            return True                 # failed

        if contents:
            store_path = bfutil.system_cache_path(self.ui, expect_hash)
            actual_hash = bfutil.hashfile(store_path)
            if actual_hash != expect_hash:
                self.ui.warn(
                    _('changeset %s: %s: contents differ\n'
                        '  (%s:\n'
                        '  expected hash %s,\n'
                        '  but got %s)\n')
                    % (cset, filename,
                        store_path, expect_hash, actual_hash))
                return True             # failed
        return False
