'''HTTP-based store.'''

import urlparse
import urllib2

from mercurial import util, url as url_
from mercurial.i18n import _

import bfutil, basestore

class httpstore(basestore.basestore):
    """A store accessed via HTTP"""
    def __init__(self, ui, repo, url):
        url = bfutil.urljoin(url, 'bfile')
        super(httpstore, self).__init__(ui, repo, url)
        self.rawurl, self.path = urlparse.urlsplit(self.url)[1:3]
        (baseurl, authinfo) = url_.getauthinfo(self.url)
        self.opener = url_.opener(self.ui, authinfo)

    def put(self, source, hash):
        self.sendfile(source, hash)
        self.ui.debug('put %s to remote store\n' % source)

    def exists(self, hash):
        return self._verify(hash)

    def sendfile(self, filename, hash):
        if self._verify(hash):
            return

        self.ui.debug('httpstore.sendfile(%s, %s)\n' % (filename, hash))
        baseurl, authinfo = url_.getauthinfo(self.url)
        fd = None
        try:
            fd = url_.httpsendfile(filename, 'rb')
            request = urllib2.Request(bfutil.urljoin(baseurl, hash), fd)
            try:
                url = self.opener.open(request)
                self.ui.note(_('[OK] %s/%s\n') % (self.rawurl, url.geturl()))
            except urllib2.HTTPError, e:
                raise util.Abort(_('unable to POST: %s\n') % e.msg)
        except Exception, e:
            raise util.Abort(_('%s') % e)
        finally:
            if fd: fd.close()

    def _getfile(self, tmpfile, filename, hash):
        (baseurl, authinfo) = url_.getauthinfo(self.url)
        url = bfutil.urljoin(baseurl, hash)
        try:
            request = urllib2.Request(url)
            infile = self.opener.open(request)
        except urllib2.HTTPError, err:
            detail = _("HTTP error: %s %s") % (err.code, err.msg)
            raise basestore.StoreError(filename, hash, url, detail)
        except urllib2.URLError, err:
            # This usually indicates a connection problem, so don't
            # keep trying with the other files... they will probably
            # all fail too.
            reason = err[0][1]      # assumes err[0] is a socket.error
            raise util.Abort('%s: %s' % (baseurl, reason))
        return bfutil.copy_and_hash(bfutil.blockstream(infile), tmpfile)

    def _verify(self, hash):
        baseurl, authinfo = url_.getauthinfo(self.url)
        store_path = bfutil.urljoin(baseurl, hash)
        request = urllib2.Request(store_path)
        request.add_header('SHA1-Request', hash)
        try:
            url = self.opener.open(request)
            if 'Content-SHA1' in url.info() and hash == url.info()['Content-SHA1']:
                return True
            else:
                return False
        except:
            return False

    def _verifyfile(self, cctx, cset, contents, standin, verified):
        baseurl, authinfo = url_.getauthinfo(self.url)
        filename = bfutil.split_standin(standin)
        if not filename:
            return False
        fctx = cctx[standin]
        key = (filename, fctx.filenode())
        if key in verified:
            return False

        expect_hash = fctx.data()[0:40]
        store_path = bfutil.urljoin(baseurl, expect_hash)
        verified.add(key)

        request = urllib2.Request(store_path)
        request.add_header('SHA1-Request',expect_hash)
        try:
            url = self.opener.open(request)
            if 'Content-SHA1' in url.info():
                rhash = url.info()['Content-SHA1']
                if rhash == expect_hash:
                    return False
                else:
                    self.ui.warn(
                        _('changeset %s: %s: contents differ\n (%s)\n')
                        % (cset, filename, store_path))
                    return True             # failed
            else:
                self.ui.warn(_('remote did not send a hash, '
                    'it probably does not understand this protocol\n'))
                return False
        except urllib2.HTTPError, e:
            if e.code == 404:
                self.ui.warn(
                    _('changeset %s: %s missing\n (%s)\n')
                    % (cset, filename, store_path))
                return True                 # failed
            else:
                raise util.Abort(_('check failed, unexpected response'
                                   'status: %d: %s') % (e.code, e.msg))

