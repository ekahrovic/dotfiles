#!/usr/bin/python

# Test revert

import os
import common

hgt = common.BfilesTester()

def checkfile(file, contents):
    hgt.asserttrue(hgt.readfile(file) == contents, 'file contents dont match')

# add size and patterns for adding as bfiles
hgt.updaterc()
hgt.announce('setup')
os.mkdir('repo1')
os.chdir('repo1')
os.mkdir('dir')
os.mkdir('dir/dir')
hgt.hg(['init', '-q'])
hgt.writefile('n1', 'n1')
hgt.writefile('n2.txt', 'n2')
hgt.writefile('dir/n3', 'n3')
hgt.writefile('dir/dir/n4.txt', 'n4')
hgt.hg(['add', 'n1', 'n2.txt', 'dir/n3', 'dir/dir/n4.txt'])
hgt.writefile('b1', 'b1')
hgt.writefile('b2.txt', 'b2')
hgt.writefile('dir/b3', 'b3')
hgt.writefile('dir/dir/b4.txt', 'b4')
hgt.hg(['add', '--bf'],
        stdout=('adding b1 as bfile\n'
                'adding b2.txt as bfile\n'
                'adding dir/b3 as bfile\n'
                'adding dir/dir/b4.txt as bfile\n'))
hgt.hg(['status'],
        stdout=('A b1\n'
                'A b2.txt\n'
                'A dir/b3\n'
                'A dir/dir/b4.txt\n'
                'A dir/dir/n4.txt\n'
                'A dir/n3\n'
                'A n1\n'
                'A n2.txt\n'))
hgt.hg(['commit', '-m', 'added files'])

hgt.writefile('n1', 'n2')
hgt.writefile('n2.txt', 'n3')
hgt.writefile('dir/n3', 'n4')
hgt.writefile('dir/dir/n4.txt', 'n5')
hgt.writefile('b1', 'b2')
hgt.writefile('b2.txt', 'b3')
hgt.writefile('dir/b3', 'b4')
hgt.writefile('dir/dir/b4.txt', 'b5')
hgt.hg(['commit', '-m', 'edit files'])
os.mkdir('txtfiles')
hgt.hg(['rename', 'glob:**.txt', 'txtfiles'],
        stdout=('moving dir/dir/n4.txt to txtfiles/n4.txt\n'
                'moving n2.txt to txtfiles/n2.txt\n'
                'moving .kbf/b2.txt to .kbf/txtfiles/b2.txt\n'
                'moving .kbf/dir/dir/b4.txt to .kbf/txtfiles/b4.txt\n'))
hgt.hg(['commit', '-m', 'rename files'])
hgt.hg(['rm', 'n1', 'dir/b3'])
hgt.hg(['commit', '-m', 'remove files'])

hgt.announce('revert all')
hgt.hg(['revert', '-a'])
hgt.hg(['status'])
hgt.hg(['revert', '-a', '-r', '0'],
    stdout='''reverting .kbf/b1
adding .kbf/b2.txt
adding .kbf/dir/b3
adding .kbf/dir/dir/b4.txt
removing .kbf/txtfiles/b2.txt
removing .kbf/txtfiles/b4.txt
adding dir/dir/n4.txt
reverting dir/n3
adding n1
adding n2.txt
removing txtfiles/n2.txt
removing txtfiles/n4.txt
''')
hgt.asserttrue(hgt.readfile('n1') == 'n1', 'file changed')
hgt.asserttrue(hgt.readfile('n2.txt') == 'n2', 'file changed')
hgt.asserttrue(hgt.readfile('dir/n3') == 'n3', 'file changed')
hgt.asserttrue(hgt.readfile('dir/dir/n4.txt') == 'n4', 'file changed')
hgt.asserttrue(hgt.readfile('b1') == 'b1', 'file changed')
hgt.asserttrue(hgt.readfile('b2.txt') == 'b2', 'file changed')
hgt.asserttrue(hgt.readfile('dir/b3') == 'b3', 'file changed')
hgt.asserttrue(hgt.readfile('dir/dir/b4.txt') == 'b4', 'file changed')
hgt.assertfalse(os.path.exists('txtfiles/n2.txt'), 'file shouldnt exist')
hgt.assertfalse(os.path.exists('txtfiles/n4.txt'), 'file shouldnt exist')
hgt.assertfalse(os.path.exists('txtfiles/b2.txt'), 'file shouldnt exist')
hgt.assertfalse(os.path.exists('txtfiles/b4.txt'), 'file shouldnt exist')

hgt.hg(['status'],
        stdout='''M b1
M dir/n3
A b2.txt
A dir/b3
A dir/dir/b4.txt
A dir/dir/n4.txt
A n1
A n2.txt
R txtfiles/b2.txt
R txtfiles/b4.txt
R txtfiles/n2.txt
R txtfiles/n4.txt
''')
hgt.hg(['revert', '-a'],
        stdout='''reverting .kbf/b1
forgetting .kbf/b2.txt
forgetting .kbf/dir/b3
forgetting .kbf/dir/dir/b4.txt
undeleting .kbf/txtfiles/b2.txt
undeleting .kbf/txtfiles/b4.txt
forgetting dir/dir/n4.txt
reverting dir/n3
forgetting n1
forgetting n2.txt
undeleting txtfiles/n2.txt
undeleting txtfiles/n4.txt
''')
hgt.asserttrue(hgt.readfile('n1') == 'n1', 'file changed')
hgt.asserttrue(hgt.readfile('n2.txt') == 'n2', 'file changed')
hgt.asserttrue(hgt.readfile('dir/n3') == 'n4', 'file changed')
hgt.asserttrue(hgt.readfile('dir/n3.orig') == 'n3', 'file changed')
hgt.asserttrue(hgt.readfile('dir/dir/n4.txt') == 'n4', 'file changed')
hgt.asserttrue(hgt.readfile('b1') == 'b2', 'file changed')
hgt.asserttrue(hgt.readfile('b1.orig') == 'b1', 'file changed')
hgt.asserttrue(hgt.readfile('b2.txt') == 'b2', 'file changed')
hgt.asserttrue(hgt.readfile('dir/b3') == 'b3', 'file changed')
hgt.asserttrue(hgt.readfile('dir/dir/b4.txt') == 'b4', 'file changed')
hgt.asserttrue(hgt.readfile('txtfiles/n2.txt') == 'n3', 'file changed')
hgt.asserttrue(hgt.readfile('txtfiles/n4.txt') == 'n5', 'file changed')
hgt.asserttrue(hgt.readfile('txtfiles/b2.txt') == 'b3', 'file changed')
hgt.asserttrue(hgt.readfile('txtfiles/b4.txt') == 'b5', 'file changed')
hgt.hg(['status'],
        stdout='''? b1.orig
? b2.txt
? dir/b3
? dir/dir/b4.txt
? dir/dir/n4.txt
? dir/n3.orig
? n1
? n2.txt
''')
os.unlink('b1.orig')
os.unlink('b2.txt')
os.unlink('dir/dir/b4.txt')
os.unlink('dir/dir/n4.txt')
os.unlink('dir/n3.orig')
os.unlink('n1')
os.unlink('n2.txt')
os.unlink('dir/b3')
os.unlink('.kbf/dir/b3')
os.unlink('.kbf/b1.orig')
os.unlink('.kbf/b2.txt')
os.unlink('.kbf/dir/dir/b4.txt')

hgt.announce('revert specific files')
hgt.hg(['revert', '-r', '1', 'glob:**.txt'],
        stdout='''adding .kbf/b2.txt
adding .kbf/dir/dir/b4.txt
removing .kbf/txtfiles/b2.txt
removing .kbf/txtfiles/b4.txt
adding dir/dir/n4.txt
adding n2.txt
removing txtfiles/n2.txt
removing txtfiles/n4.txt
''')
hgt.asserttrue(hgt.readfile('n2.txt') == 'n3', 'file changed')
hgt.asserttrue(hgt.readfile('dir/n3') == 'n4', 'file changed')
hgt.asserttrue(hgt.readfile('dir/dir/n4.txt') == 'n5', 'file changed')
hgt.asserttrue(hgt.readfile('b1') == 'b2', 'file changed')
hgt.asserttrue(hgt.readfile('b2.txt') == 'b3', 'file changed')
hgt.asserttrue(hgt.readfile('dir/dir/b4.txt') == 'b5', 'file changed')
hgt.assertfalse(os.path.exists('txtfiles/n2.txt'), 'file shouldnt exist')
hgt.assertfalse(os.path.exists('txtfiles/n4.txt'), 'file shouldnt exist')
hgt.assertfalse(os.path.exists('txtfiles/b2.txt'), 'file shouldnt exist')
hgt.assertfalse(os.path.exists('txtfiles/b4.txt'), 'file shouldnt exist')
hgt.hg(['status'],
        stdout='''A b2.txt
A dir/dir/b4.txt
A dir/dir/n4.txt
A n2.txt
R txtfiles/b2.txt
R txtfiles/b4.txt
R txtfiles/n2.txt
R txtfiles/n4.txt
''')
hgt.hg(['revert', '-r', '0', 'n2.txt', 'b2.txt'])
hgt.asserttrue(hgt.readfile('n2.txt') == 'n2', 'file changed')
hgt.asserttrue(hgt.readfile('dir/n3') == 'n4', 'file changed')
hgt.asserttrue(hgt.readfile('dir/dir/n4.txt') == 'n5', 'file changed')
hgt.asserttrue(hgt.readfile('b1') == 'b2', 'file changed')
hgt.asserttrue(hgt.readfile('b2.txt') == 'b2', 'file changed')
hgt.asserttrue(hgt.readfile('dir/dir/b4.txt') == 'b5', 'file changed')
hgt.assertfalse(os.path.exists('txtfiles/n2.txt'), 'file shouldnt exist')
hgt.assertfalse(os.path.exists('txtfiles/n4.txt'), 'file shouldnt exist')
hgt.assertfalse(os.path.exists('txtfiles/b2.txt'), 'file shouldnt exist')
hgt.assertfalse(os.path.exists('txtfiles/b4.txt'), 'file shouldnt exist')
hgt.hg(['status'],
        stdout='''A b2.txt
A dir/dir/b4.txt
A dir/dir/n4.txt
A n2.txt
R txtfiles/b2.txt
R txtfiles/b4.txt
R txtfiles/n2.txt
R txtfiles/n4.txt
? b2.txt.orig
? n2.txt.orig
''')
