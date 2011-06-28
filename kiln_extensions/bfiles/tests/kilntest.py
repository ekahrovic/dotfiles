import json
import sys
import time
import urllib2
from urllib import urlencode

# Update the four variables below
# Your local kiln must have a project Test with a repo group called Test
# The repo test in that group will be used for testing. The username
# and password given must have write access to that group.

KILNAUTHPATH = '/code/kiln/2-0/extensions/kilnauth.py'
KILNURL = 'http://localhost/FogBugz/kiln'
USER = 'test'
PASSWORD = 'tester'


def api(url):
    return KILNURL + '/api/1.0/' + url

def slurp(url, params={}, post=False, raw=False):
    params = urlencode(params, doseq=True)
    handle = urllib2.urlopen(url, params) if post else urllib2.urlopen(url + '?' + params)
    content = handle.read()
    obj = content if raw else json.loads(content)
    handle.close()
    return obj

def gettoken():
     return slurp(api('Auth/Login'), dict(sUser='test', sPassword='tester'))

def createtest(hgt, token):
    projects = slurp(api('Project'), dict(token=token))

    found = False
    for project in projects:
        if project['sName'] == 'Test':
            ixProject = project['ixProject']
            for group in project['repoGroups']:
                if group['sName'] == 'Test':
                    ixRepoGroup = group['ixRepoGroup']
                    found = True

    if not found:
        return None

    repo = slurp(api('Repo/Create'), dict(sName='Test', sDescription='test', ixRepoGroup=ixRepoGroup, sDefaultPermission='write', token=token))
    ixRepo = repo['ixRepo']

    hgt.asserttrue(isinstance(ixRepo, int), 'Create failed %s' % (str(ixRepo)))
    
    time.sleep(1)
    while slurp(api('Repo/%d' % ixRepo), dict(token=token))['sStatus'] != 'good':
        time.sleep(0.1)

    return (KILNURL + '/Repo/Test/Test/test', ixRepo)

def deletetest(hgt, token):
    projects = slurp(api('Project'), dict(token=token))

    found = False
    for project in projects:
        if project['sName'] == 'Test':
            ixProject = project['ixProject']
            for group in project['repoGroups']:
                if group['sName'] == 'Test':
                    ixRepoGroup = group['ixRepoGroup']
                    for repo in group['repos']:
                        if repo['sName'] == 'Test':
                            ixRepo = repo['ixRepo']
                            found = True
    if not found:
        return None
    slurp(api('Repo/%d/Delete' % ixRepo), dict(token=token), post=True)

    try:
        while True:
            slurp(api('Repo/%d' % ixRepo), dict(token=token))
            time.sleep(0.1)
    except urllib2.HTTPError:
        pass 

