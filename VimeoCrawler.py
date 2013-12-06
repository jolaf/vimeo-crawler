#!/usr/bin/python
from datetime import datetime
from getopt import getopt
from itertools import count
from logging import getLogger, Formatter, FileHandler, StreamHandler, DEBUG, INFO, WARNING
from re import match
from os import chmod, makedirs, remove, stat
from os.path import abspath, isdir, isfile, join, lexists
from subprocess import Popen
from sys import argv, exit, platform, stdout # pylint: disable=W0622
from traceback import format_exc

try: # Selenium configuration
    import selenium
    if selenium.__version__.split('.') < ['2', '32', '0']:
        raise ImportError('Selenium version %s < 2.32.0' % selenium.__version__)
    from selenium import webdriver
    from selenium.common.exceptions import NoSuchElementException
    DRIVERS = dict((v.lower(), (v, getattr(webdriver, v))) for v in vars(webdriver) if v[0].isupper()) # ToDo: Make this list more precise
except ImportError, ex:
    print "%s: %s\nERROR: This software requires Selenium.\nPlease install Selenium v2.32.0 or later: https://pypi.python.org/pypi/selenium\n" % (ex.__class__.__name__, ex)
    exit(-1)

try: # Filesystem symbolic links configuration
    from os import symlink # UNIX # pylint: disable=E0611
except ImportError:
    global symlink # pylint: disable=W0604
    try:
        import ctypes
        dll = ctypes.windll.LoadLibrary('kernel32.dll')
        def symlink(source, linkName):
            if not dll.CreateSymbolicLinkW(linkName, source, 0):
                raise OSError("code %d" % dll.GetLastError())
    except Exception, ex:
        symlink = None
        print "%s: %s\nWARNING: Filesystem links will not be available.\nPlease run on UNIX or Windows Vista or later.\n" % (ex.__class__.__name__, ex)

try: # Requests HTTP library
    import requests
    if requests.__version__.split('.') < ['1', '2', '0']:
        raise ImportError('Requests version %s < 1.2.0' % requests.__version__)
except ImportError, ex:
    requests = None
    print "%s: %s\nWARNING: Video size information will not be available.\nPlease install Requests v1.2.0 or later: https://pypi.python.org/pypi/requests\n" % (ex.__class__.__name__, ex)

SE = stdout.encoding
TIME_FORMAT = '%Y-%m-%d %H:%M:%S'

isWindows = platform.lower().startswith('win')
EMPTY_ECHO = '.' if isWindows else ''
DOWNLOAD_SCRIPT = 'download.cmd' if isWindows else 'download.sh'
DOWNLOAD_HEADER = ('@echo off\n' if isWindows else '#/bin/sh\n') + '''\
echo%s
echo VimeoCrawler download script
echo Generated at %%s
echo%s
''' % (EMPTY_ECHO, EMPTY_ECHO)
DOWNLOAD_COMMAND = 'wget --header "%c" -t %r --retry-connrefused -c -O "%t" "%s"'
SCRIPT_COMMAND = '''\
echo %%s
echo %%s
echo%s
%%s
echo %%s
echo%s
''' % (EMPTY_ECHO, EMPTY_ECHO)

TITLE = 'VimeoCrawler v1.13 (c) 2013 Vasily Zakharov vmzakhar@gmail.com'

OPTION_NAMES = ('download', 'login', 'max-items', 'retries', 'target', 'webdriver')
FIELD_NAMES = ('downloadCommandTemplate', 'credentials', 'maxItems', 'retryCount', 'targetDirectory', 'driverName')
SHORT_OPTIONS = ''.join(('%c:' % option[0]) for option in OPTION_NAMES) + 'fghsv'
LONG_OPTIONS = tuple(('%s=' % option) for option in OPTION_NAMES) + ('no-folders', 'go', 'help', 'no-sizes', 'verbose')

USAGE_INFO = '''Usage: python VimeoCrawler.py [options] [start URL or video ID]

The crawler checks the specified URL and processes the specified video,
album, channel or the whole account, trying to locate the highest available
quality file for each video.

For every video found a zero-sized file is created in the target directory.
For any channel or album encountered, a subfolder is created in the target
directory, with symbolic links to the files in the target directory.

Also, a download script is created, that can be run to download all the videos.
Both the crawler and the download script can be run repeatedly with the same
start URL and target directory to update the list of videos, download the newly
added videos and continue the interrupted downloads.

In default configuration, the program requires Mozilla Firefox and wget tool
to download the videos.

Options:
-h --help - Displays this help message.
-v --verbose - Provide verbose logging.
-f --no-folders - Do not create subfolders with links for channels and albums.
-s --no-sizes - Do not get file sizes for videos (speeds up crawling a bit).
-g --go - Run the generated download script after crawling completes.

-l --login - Vimeo login credentials, formatted as email:password.
-t --target - Target directory to save all the output files to,
              default is the current directory.

-w --webdriver - Selenium WebDriver to use for crawling, defaults to Firefox.
-r --retries - Number of attempts to reload a page that seems failed to load.
-m --max-items - Maximum number of items (videos or folders) to retrieve
                 from one page (usable for testing).

-d --download - A command to use to download a file, remember to quote it.
The default is: %s
                %%s inserts the URL to download
                %%t inserts the output file name
                %%c inserts Vimeo cookie
                %%r inserts number of retries in case of error

If start URL is not specified, the login credentials have to be specified.
In that case, the whole account for those credentials would be crawled.
''' % DOWNLOAD_COMMAND

def usage(error = None):
    '''Prints usage information (preceded by optional error message) and exits with code 2.'''
    print "%s\n" % TITLE
    print USAGE_INFO
    if error:
        print error
    exit(2 if error else 0)

LOG_FILE_NAME = 'VimeoCrawler.log'

VIMEO = 'vimeo.com'
VIMEO_URL = 'https://%s/%%s' % VIMEO
COOKIE = 'vimeo'

SYSTEM_LINKS = ('about', 'blog', 'categories', 'channels', 'cookie_policy', 'couchmode', 'creativecommons', 'creatorservices', 'dmca', 'enhancer', 'everywhere', 'explore', 'groups', 'help', 'jobs', 'join', 'log_in', 'love', 'musicstore', 'ondemand', 'plus', 'privacy', 'pro', 'robots.txt', 'search', 'site_map', 'staffpicks', 'terms', 'upload', 'videoschool') # http://vimeo.com/link
CATEGORIES_LINKS = ('albums', 'groups', 'channels') # http://vimeo.com/account/category
VIDEOS_LINKS = ('videos') # http://vimeo.com/account/videos URLs
FOLDERS_LINKS = ('album', 'groups', 'channels') # http://vimeo.com/folder/*
FOLDER_NAMES = {'albums': 'album', 'groups': 'group', 'channels': 'channel'} # Mapping to singular for printing
FILE_PREFERENCES = ('Original', 'HD', 'SD', 'Mobile', 'file') # Vimeo file versions parts

UNITS = ('bytes', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB')
def readableSize(size):
    size = float(int(size))
    for (i, unit) in enumerate(UNITS):
        if size < 1024 or i == len(UNITS) - 1:
            break
        size /= 1024
    fSize = '%.1f' % size
    if len(fSize) > 3:
        fSize = '%.0f' % size
    return '%s %s' % (fSize, unit) # pylint: disable=W0631

INVALID_FILENAME_CHARS = ':/\\\'"?*' # for file names, to be replaced with _
def cleanupFileName(fileName):
    return ''.join('_' if c in INVALID_FILENAME_CHARS else c for c in fileName)

WINDOWS_INVALID_SHELL_CHARS = '<>'
UNIX_INVALID_SHELL_CHARS = '\\"`$'
def cleanupForShell(s):
    if isWindows:
        for c in WINDOWS_INVALID_SHELL_CHARS:
            s = s.replace(c, '^' + c)
        return s.replace('%', '%%')
    for c in UNIX_INVALID_SHELL_CHARS:
        s = s.replace(c, '\\' + c)
    return '"%s"' % s

class URL(object):
    FILE_NAME = 'source.url'
    def __init__(self, url):
        if hasattr(url, 'url'):
            url = url.url
        if '/' not in str(url):
            url = VIMEO_URL % url
        self.url = str(url).strip().strip('/')
        slashIndex = self.url.find('/') + 1
        self.url = self.url[:slashIndex] + self.url[slashIndex:].replace('//', '/')
        url = self.url.lower()
        if VIMEO not in url:
            raise ValueError("Invalid Vimeo URL: %s" % url)
        tokens = url[url.index(VIMEO) + len(VIMEO) + 1:].split('/')
        if len(tokens) in (3, 4) and tokens[-1].isdigit():
            self.url = VIMEO_URL % tokens[-1]
            tokens = tokens[-1:]
        if len(tokens) == 3 and tokens[-1] == 'videos':
            tokens = tokens[:-1]
        self.isSystem   = not tokens or tokens[0] in SYSTEM_LINKS and (len(tokens) == 1 or tokens[0] not in FOLDERS_LINKS)
        self.isVideo    = len(tokens) == 1 and tokens[0].isdigit()
        self.isAccount  = len(tokens) == 1 and not self.isSystem and not self.isVideo
        self.isCategory = len(tokens) == 2 and tokens[1] in CATEGORIES_LINKS
        self.isVideos   = len(tokens) == 2 and tokens[1] in VIDEOS_LINKS
        self.isFolder   = len(tokens) == 2 and tokens[0] in FOLDERS_LINKS
        self.vID = int(tokens[0]) if self.isVideo else None
        self.account   = tokens[0]  if self.isAccount or self.isCategory or self.isVideos else None
        self.category  = tokens[1]  if self.isCategory or self.isVideos else None
        self.folder    = tokens[0]  if self.isFolder else None
        self.name      = tokens[1]  if self.isFolder else self.account if self.isAccount else self.category if self.isCategory or self.isVideos else None
        if self.isFolder and self.folder != 'album' and not self.url.endswith('videos'):
            self.url += '/videos'

    def createFile(self, directory):
        with open(join(directory, self.FILE_NAME), 'w') as f:
            f.write('[InternetShortcut]\nURL=%s\n' % self.url.split('/videos')[0])

    def __str__(self):
        return self.url

    def __repr__(self):
        return "URL(%s)" % repr(self.url)

    def __hash__(self):
        return hash(self.url)

    def __cmp__(self, other):
        return 1 if self.url > other.url else -1 if self.url < other.url else 0

class VimeoDownloader(object): # pylint: disable=R0902
    def __init__(self, args):
        # Simple options
        self.go = False
        self.verbose = False
        self.foldersNeeded = True
        self.getFileSizes = bool(requests)
        # Options with arguments
        self.driver = None
        self.driverName = 'Firefox'
        self.driverClass = None
        self.credentials = None
        self.maxItems = None
        self.retryCount = 5
        self.targetDirectory = ''
        self.downloadScriptFileName = None
        self.downloadCommandTemplate = DOWNLOAD_COMMAND
        self.startURL = None
        try:
            # Reading command line options
            (options, parameters) = getopt(args, SHORT_OPTIONS, LONG_OPTIONS)
            for (option, value) in options:
                if option in ('-h', '--help'):
                    usage()
                elif option in ('-g', '--go'):
                    self.go = True
                elif option in ('-v', '--verbose'):
                    self.verbose = True
                elif option in ('-f', '--no-folders'):
                    self.foldersNeeded = False
                elif option in ('-s', '--no-filesize'):
                    self.getFileSizes = False
                else: # Parsing options with arguments
                    index = None
                    for (maskNum, mask) in enumerate(('-([^-])', '--(.*)')):
                        m = match(mask, option)
                        if not m:
                            continue
                        index = tuple(OPTION_NAMES.index(option) for option in OPTION_NAMES if (option if maskNum else option[0]) == m.group(1))
                        break
                    else:
                        assert False # This should never happen
                    assert len(index) == 1
                    setattr(self, FIELD_NAMES[index[0]], value)
            # Processing command line options
            driverTuple = DRIVERS.get(self.driverName.lower())
            if not driverTuple:
                raise ValueError("Unknown driver %s, valid values are: %s" % (self.driverName, '/'.join(sorted(x[0] for x in DRIVERS.itervalues()))))
            (self.driverName, self.driverClass) = driverTuple
            if self.credentials:
                try:
                    index = self.credentials.index(':', self.credentials.index('@'))
                    self.credentials = (self.credentials[0 : index], self.credentials[index + 1:])
                except ValueError:
                    raise ValueError("-l / --login parameter must be formatted as follows: user.name@host.name:password")
            if self.maxItems:
                try:
                    self.maxItems = int(self.maxItems)
                    if self.maxItems < 0:
                        raise ValueError
                except ValueError:
                    raise ValueError("-m / --max-items parameter must be a non-negative integer")
            try:
                self.retryCount = int(self.retryCount)
                if self.retryCount < 0:
                    raise ValueError
            except ValueError:
                raise ValueError("-r / --retries parameter must be non-negative integer")
            if len(parameters) > 1:
                raise Exception("Too many parameters")
            if parameters:
                self.startURL = URL(parameters[0])
            elif not self.credentials:
                raise ValueError("Neither login credentials nor start URL is specified")
            # Creating target directory
            if self.targetDirectory == '.':
                self.targetDirectory = ''
            self.createDir()
            if self.startURL:
                self.startURL.createFile(self.targetDirectory)
            self.downloadScriptFileName = join(self.targetDirectory, DOWNLOAD_SCRIPT)
            # Configuring logging
            rootLogger = getLogger()
            if not rootLogger.handlers:
                formatter = Formatter("%(asctime)s %(levelname)s %(message)s", '%Y-%m-%d %H:%M:%S')
                streamHandler = StreamHandler()
                streamHandler.setFormatter(formatter)
                fileHandler = FileHandler(join(self.targetDirectory, LOG_FILE_NAME), mode = 'w')
                fileHandler.setFormatter(formatter)
                rootLogger.addHandler(streamHandler)
                rootLogger.addHandler(fileHandler)
            rootLogger.setLevel(DEBUG if self.verbose else WARNING)
            self.logger = getLogger('vimeo')
            self.logger.setLevel(DEBUG if self.verbose else INFO)
            self.logger.info(TITLE)
        except Exception, e:
            usage("ERROR: %s\n" % e)

    def createDir(self, dirName = None):
        dirName = join(self.targetDirectory, dirName) if dirName else self.targetDirectory
        if dirName and not isdir(dirName):
            makedirs(dirName)
        return dirName

    def goTo(self, url):
        url = URL(url)
        self.logger.info("Going to %s", url)
        self.driver.get(url.url)

    def getElement(self, css):
        return self.driver.find_element_by_css_selector(css)

    def login(self, email, password):
        self.goTo('http://vimeo.com/log_in')
        self.logger.info("Logging in as %s...", email)
        try:
            self.getElement('#email').send_keys(email)
            self.getElement('#password').send_keys(password)
            self.getElement('#login_form input[type=submit]').click()
            self.getElement('#menu .me a').click()
            self.loggedIn = True
        except NoSuchElementException, e:
            self.logger.error("Login failed: %s", e.msg)
            self.errors += 1

    def getItemsFromPage(self):
        self.logger.info("Processing %s", self.driver.current_url)
        try:
            links = self.driver.find_elements_by_css_selector('#browse_content .browse a')
            links = tuple(link.get_attribute('href') for link in links)
            items = tuple(URL(link) for link in links if VIMEO in link and not link.endswith('settings'))[:self.maxItems]
        except NoSuchElementException, e:
            self.logger.error(e.msg)
            self.errors += 1
            items = ()
        numVideos = len(tuple(item for item in items if item.isVideo))
        if numVideos:
            if numVideos == len(items):
                self.logger.info("Got %d videos", numVideos)
            else:
                self.logger.info("Got %d videos and %d other items", numVideos, len(items) - numVideos)
        else:
            self.logger.info("Got %d items", len(items))
        assert len(items) == len(set(items))
        return items

    def getItemsFromFolder(self):
        items = []
        for i in xrange(self.maxItems) if self.maxItems != None else count(): # pylint: disable=W0612
            items.extend(self.getItemsFromPage())
            try:
                self.getElement('.pagination a[rel=next]').click()
            except NoSuchElementException:
                break
        items = tuple(items)
        assert len(items) == len(set(items))
        return items

    def getItemsFromURL(self, url = None, target = None):
        url = URL(url or self.driver.current_url)
        if not self.startURL:
            self.startURL = url
            self.startURL.createFile(self.targetDirectory)
        items = ()
        if url.isVideo: # Video
            if url.vID not in self.vIDs:
                self.vIDs.append(url.vID)
            if target != None:
                target.add(url.vID)
        elif url.isAccount: # Account main page
            self.goTo(url.url + '/videos')
            self.logger.info("Processing account %s", url.account)
            items = self.getItemsFromFolder() + (url.url + '/channels', url.url + '/albums')
            self.doCreateFolders = self.foldersNeeded
        elif url.isVideos: # Videos
            self.goTo(url)
            items = self.getItemsFromFolder()
        elif url.isCategory: # Category
            self.goTo(url)
            items = self.getItemsFromFolder()
            self.doCreateFolders = self.foldersNeeded
        elif url.isFolder: # Folder
            title = None
            for i in xrange(self.retryCount + 1):
                self.goTo(url)
                try:
                    title = self.getElement('#page_header h1 a').text
                except NoSuchElementException:
                    try:
                        title = self.getElement('#page_header h1').text
                    except NoSuchElementException:
                        try:
                            title = self.getElement('#group_header h1 a').get_attribute('title')
                        except NoSuchElementException:
                            try:
                                title = self.getElement('#group_header h1 a').text
                            except NoSuchElementException, e:
                                self.logger.warning(e.msg)
                                if i >= self.retryCount:
                                    self.logger.error("Page load failed")
                                    self.errors += 1
                if title:
                    self.logger.info("Folder: %s", title.encode(SE, 'replace'))
                    if self.doCreateFolders:
                        dirName = self.createDir(cleanupFileName(title.strip().rstrip('.'))) # unicode
                        url.createFile(dirName)
                        if symlink:
                            target = set()
                            self.folders.append((dirName, target))
                    items = self.getItemsFromFolder()
                    break
        else: # Some other page
            self.goTo(url)
            items = self.getItemsFromPage()
        for item in items:
            self.getItemsFromURL(item, target)

    def processVideo(self, vID, number):
        title = ''
        download = None
        # Parse video page
        for i in count():
            try:
                self.goTo(vID)
                title = self.getElement('h1[itemprop=name]').text.strip().rstrip('.').encode(SE, 'replace')
                self.driver.find_element_by_link_text('Download').click()
                download = self.getElement('#download')
                break
            except NoSuchElementException, e:
                self.logger.warning(e.msg)
                if i >= self.retryCount:
                    self.logger.error("Page load failed")
                    self.errors += 1
                    break
        # Parse download links
        link = None
        if download:
            for preference in FILE_PREFERENCES:
                try:
                    link = download.find_element_by_partial_link_text(preference)
                    break
                except NoSuchElementException:
                    pass
        if link: # Parse chosen download link
            tokens = link.text.split() # unicode
            extension = tokens[1].strip('.') # unicode
            description = ('%s/%s' % (tokens[0], extension.upper())).encode(SE, 'replace')
            link = str(link.get_attribute('href'))
            if self.getFileSizes:
                try:
                    request = requests.get(link, stream = True)
                    request.close()
                    fileSize = int(request.headers['content-length'])
                    self.totalFileSize += fileSize
                    description += ', %s' % readableSize(fileSize)
                except Exception, e:
                    self.logger.warning(e)
        else:
            description = extension = 'NONE'
        # Prepare file information
        prefix = ' '.join((title, '(%s)' % description))
        suffix = ' '.join((('%d/%d %d%%' % (number, len(self.vIDs), int(number * 100.0 / len(self.vIDs)))),)
                        + ((readableSize(self.totalFileSize),) if self.totalFileSize else ()))
        self.logger.info(' '.join((prefix, suffix)))
        fileName = cleanupFileName('%s.%s' % (' '.join(((title.decode(SE),) if title else ()) + (str(vID),)), extension.lower())) # unicode
        # Creating target file, if it doesn't exist
        targetFileName = join(self.targetDirectory, fileName)
        if not isfile(targetFileName):
            open(targetFileName, 'w').close()
        if link: # Creating download script entry
            prefix = cleanupForShell('Downloading ' + prefix)
            suffix = cleanupForShell('Downloaded ' + suffix)
            command = self.downloadCommand.replace('%s', link).replace('%t', fileName.encode(SE))
            self.downloadScript.write(SCRIPT_COMMAND % (prefix, cleanupForShell('> ' + command), command, suffix))
        # Creating symbolic links, if enabled
        for dirName in (dirName for (dirName, vIDs) in self.folders if vID in vIDs):
            linkFileName = join(dirName, fileName) # unicode
            try:
                if lexists(linkFileName):
                    remove(linkFileName)
            except:
                pass
            try:
                symlink(join('..', fileName), linkFileName)
            except Exception, e:
                self.logger.warning("Can't create link at %s: %s", linkFileName.encode(SE), e)
                self.errors += 1

    def run(self):
        self.doCreateFolders = False
        self.loggedIn = False
        self.vIDs = []
        self.folders = []
        self.totalFileSize = 0
        self.downloadCommand = None
        self.downloadScript = None
        self.errors = 0
        try:
            self.logger.info("Starting %s..." % self.driverName)
            self.driver = self.driverClass() # ToDo: Provide parameters to the driver
            if self.credentials:
                self.login(*self.credentials)
            if not self.loggedIn and not self.startURL:
                raise ValueError("Login failed and no start URL is specified, aborting")
            self.getItemsFromURL(self.startURL)
            if self.folders:
                self.logger.info("Got total of %d folders", len(self.folders))
            if self.vIDs:
                assert len(self.vIDs) == len(set(self.vIDs))
                self.logger.info("Processing %d videos...", len(self.vIDs))
                cookie = self.driver.get_cookie(COOKIE)
                self.downloadCommand = self.downloadCommandTemplate.replace('%c', 'Cookie: %s=%s' % (COOKIE, str(cookie['value'])) if cookie else '').replace('%r', str(self.retryCount))
                self.downloadScript = open(self.downloadScriptFileName, 'w')
                self.downloadScript.write(DOWNLOAD_HEADER % datetime.now().strftime(TIME_FORMAT))
                if self.getFileSizes:
                    requests.adapters.DEFAULT_RETRIES = self.retryCount
                for (n, vID) in enumerate(self.vIDs, 1):
                    self.processVideo(vID, n)
                self.downloadScript.close()
                chmod(self.downloadScriptFileName, stat(self.downloadScriptFileName).st_mode | 0o111) # chmod a+x
        except Exception, e:
            self.logger.error(format_exc() if self.verbose else e)
            self.errors += 1
        finally:
            if self.driver:
                self.driver.close()
        self.logger.info("Crawling completed"
                       + (' with %d errors' % self.errors if self.errors else '')
                       + (", download script saved to %s" % self.downloadScriptFileName if self.downloadScript else ''))
        if self.go and self.vIDs and not self.errors:
            try:
                self.logger.info("Running download script...")
                subprocess = Popen(abspath(self.downloadScriptFileName), cwd = self.targetDirectory or '.', shell = True)
                subprocess.communicate()
                self.logger.info("Done with code %d", subprocess.returncode) # pylint: disable=E1101
                if subprocess.returncode: # pylint: disable=E1101
                    raise Exception('code %d' % subprocess.returncode) # pylint: disable=E1101
            except Exception, e:
                self.logger.error(format_exc() if self.verbose else e)
                self.errors += 1
        return self.errors

def main(args):
    exit(1 if VimeoDownloader(args).run() else 0)

if __name__ == '__main__':
    main(argv[1:])
