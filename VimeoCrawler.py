#!/usr/bin/python
from getopt import getopt
from itertools import count
from logging import getLogger, Formatter, FileHandler, StreamHandler, INFO, NOTSET, WARNING
from re import match
from os import chmod, makedirs, stat
from os.path import abspath, isdir, isfile, join, lexists
from subprocess import Popen
from sys import argv, exit, platform, stdout # pylint: disable=W0622

try: # Selenium configuration
    import selenium
    if selenium.__version__.split('.') < ['2', '32', '0']:
        raise ImportError('Selenium version %s < 2.32.0' % selenium.__version__)
    from selenium import webdriver
    from selenium.common.exceptions import NoSuchElementException
    DRIVERS = dict((v.lower(), (v, getattr(webdriver, v))) for v in vars(webdriver) if v[0].isupper()) # ToDo: Make this list better
except ImportError, ex:
    print "%s: %s\nERROR: This software requires Selenium.\nPlease install Selenium v2.32.0 or later: https://pypi.python.org/pypi/selenium\n" % (ex.__class__.__name__, ex)
    exit(-1)

try: # Filesystem symbolic links configuration
    from os import symlink # UNIX # pylint: disable=E0611
except ImportError:
    try:
        import ctypes
        KERNEL_DLL = ctypes.windll.LoadLibrary('kernel32.dll')
        global symlink # pylint: disable=W0604
        def symlink(source, link_name):
            KERNEL_DLL.CreateSymbolicLinkW(link_name, source, 0)
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

isWindows = platform.lower().startswith('win')
DOWNLOAD_SCRIPT = 'download.cmd' if isWindows else 'download.sh'
DOWNLOAD_HEADER = '@echo off\n' if isWindows else '#/bin/sh\n'
DOWNLOAD_COMMAND = 'wget --header "%c" -t %r --retry-connrefused -c -O "%t" "%s"'

TITLE = 'VimeoCrawler v1.0 (c) 2013 Vasily Zakharov vmzakhar@gmail.com'

OPTION_NAMES = ('download', 'login', 'max-items', 'retries', 'target', 'webdriver')
FIELD_NAMES = ('downloadCommand', 'credentials', 'maxItems', 'retryCount', 'targetDirectory', 'driverName')
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
VIDEO_URL = 'https://%s/%%s' % VIMEO
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

INVALID_CHARS = ':/\\\'"?*' # for file names, to be replaced with _
def cleanupFileName(fileName):
    return ''.join('_' if c in INVALID_CHARS else c for c in fileName.strip().rstrip('.'))

class URL(object):
    FILE_NAME = 'source.url'
    def __init__(self, url):
        if hasattr(url, 'url'):
            url = url.url
        if url.isdigit():
            url = VIDEO_URL % url
        self.url = str(url).strip().strip('/')
        url = self.url.lower()
        assert VIMEO in url, url
        tokens = url[url.index(VIMEO) + len(VIMEO) + 1:].split('/')
        if len(tokens) in (3, 4) and tokens[-1].isdigit():
            self.url = VIDEO_URL % tokens[-1]
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
        return "URL('%s')" % self.url

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
        self.doCreateFolders = False
        self.getFileSizes = bool(requests)
        # Options with arguments
        self.driver = None
        self.driverName = 'Firefox'
        self.driverClass = None
        self.credentials = None
        self.maxItems = None
        self.retryCount = '5'
        self.targetDirectory = '.'
        self.downloadCommand = DOWNLOAD_COMMAND
        self.startURL = None
        # Initial values
        self.loggedIn = False
        self.vIDs = []
        self.folders = []
        self.videos = {}
        self.totalFileSize = 0
        self.errors = 0
        self.downloadScriptFileName = None
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
                if self.maxItems.isdigit(): # pylint: disable=E1101
                    self.maxItems = int(self.maxItems)
                else:
                    raise ValueError("-m / --max-items parameter must be integer")
            if self.retryCount.isdigit():
                self.retryCount = int(self.retryCount)
            else:
                raise ValueError("-r / --retries parameter must be integer")
            if len(parameters) > 1:
                raise Exception("Too many parameters")
            if parameters:
                self.startURL = URL(parameters[0])
            elif not self.credentials:
                raise ValueError("Neither login credentials nor start URL is specified")
            if not isdir(self.targetDirectory):
                makedirs(self.targetDirectory)
            # Logging configuration
            formatter = Formatter("%(asctime)s %(levelname)s %(message)s", '%Y-%m-%d %H:%M:%S')
            streamHandler = StreamHandler()
            streamHandler.setFormatter(formatter)
            fileHandler = FileHandler(join(self.targetDirectory, LOG_FILE_NAME), mode = 'w')
            fileHandler.setFormatter(formatter)
            rootLogger = getLogger()
            rootLogger.addHandler(streamHandler)
            rootLogger.addHandler(fileHandler)
            rootLogger.setLevel(WARNING)
            self.logger = getLogger('vimeo')
            self.logger.setLevel(NOTSET if self.verbose else INFO)
            self.logger.info(TITLE)
        except Exception, e:
            usage("ERROR: %s\n" % e)

    def getElement(self, css):
        return self.driver.find_element_by_css_selector(css)

    def goTo(self, url):
        url = URL(url)
        self.logger.info("Going to %s", url)
        self.driver.get(url.url)

    def login(self, email, password):
        self.goTo('http://vimeo.com/log_in')
        self.logger.info("Logging in as %s...", email)
        try:
            self.getElement('#email').send_keys(email)
            self.getElement('#password').send_keys(password)
            self.getElement('#login_form input[type=submit]').click()
            self.getElement('#menu .me a') # Make sure login is successful
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
        if url:
            url = URL(url)
        else: # No start URL, assume we've just logged in, downloading the whole account
            self.getElement('#menu .me a').click()
            url = URL(self.driver.current_url)
        if not self.startURL:
            self.startURL = url
        items = ()
        if url.isVideo: # Video
            if url.vID not in self.vIDs:
                self.vIDs.append(url.vID)
            if target:
                target.append(url.vID)
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
                    self.logger.info("Folder: %s", title.encode(stdout.encoding, 'replace'))
                    if self.doCreateFolders:
                        target = [(cleanupFileName(title), url)]
                        self.folders.append(target)
                    items = self.getItemsFromFolder()
                    break
        else: # Some other page
            self.goTo(url)
            items = self.getItemsFromPage()
        for item in items:
            self.getItemsFromURL(item, target)

    def processVideo(self, vID, percent = None):
        def getPreferredLink(element):
            for preference in FILE_PREFERENCES:
                try:
                    return element.find_element_by_partial_link_text(preference)
                except NoSuchElementException:
                    pass
            return None
        title = ''
        link = None
        for i in xrange(self.retryCount + 1):
            try:
                self.goTo(VIDEO_URL % vID)
                title = self.getElement('h1[itemprop=name]').text
                self.driver.find_element_by_link_text('Download').click()
                download = self.getElement('#download_videos')
                link = getPreferredLink(download)
                break
            except NoSuchElementException, e:
                self.logger.warning(e.msg)
                if i >= self.retryCount:
                    self.logger.error("Page load failed")
                    self.errors += 1
        if link:
            tokens = link.text.split()
            extension = tokens[1].strip('.')
            description = '%s/%s' % (tokens[0], extension.upper())
            link = link.get_attribute('href')
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
        self.logger.info(' '.join((title, '(%s)' % description, '%d/%d' % (len(self.videos) + 1, len(self.vIDs)))
                                + (('%d%%' % percent,) if percent != None else ())
                                + ((readableSize(self.totalFileSize),) if self.totalFileSize else ())).encode(stdout.encoding, 'replace'))
        fileName = cleanupFileName('%s.%s' % (' '.join((str(vID),) + ((title.encode(stdout.encoding, 'replace').decode(stdout.encoding),) if title else ())), extension.lower()))
        self.videos[vID] = (fileName, link)

    def processVideos(self):
        if self.folders:
            self.logger.info("Got total of %d folders", len(self.folders))
        self.logger.info("Processing %d videos...", len(self.vIDs))
        assert len(self.vIDs) == len(set(self.vIDs))
        if self.getFileSizes:
            requests.adapters.DEFAULT_RETRIES = self.retryCount
        for (n, vID) in enumerate(self.vIDs, 1):
            self.processVideo(vID, int(n * 100.0 / len(self.vIDs)))

    def createDir(self, dirName = None, url = None):
        dirName = join(self.targetDirectory, dirName) if dirName else self.targetDirectory
        if not isdir(dirName):
            makedirs(dirName)
        if url:
            url.createFile(dirName)
        return dirName

    def createFiles(self):
        self.logger.info("Creating files...")
        dirName = self.createDir(url = self.startURL)
        cookie = self.driver.get_cookie(COOKIE)
        downloadCommand = self.downloadCommand.replace('%c', 'Cookie: %s=%s' % (COOKIE, str(cookie['value'])) if cookie else '').replace('%r', str(self.retryCount)) + '\n'
        downloadScriptBody = DOWNLOAD_HEADER
        for vID in self.vIDs:
            (fileName, link) = self.videos[vID]
            downloadScriptBody += downloadCommand.replace('%s', link).replace('%t', fileName)
            fileName = join(dirName, fileName)
            if not isfile(fileName):
                open(fileName, 'w').close() # Touch target file
        self.downloadScriptFileName = join(dirName, DOWNLOAD_SCRIPT)
        with open(self.downloadScriptFileName, 'w') as f:
            f.write(downloadScriptBody.encode(stdout.encoding))
            chmod(self.downloadScriptFileName, stat(self.downloadScriptFileName).st_mode | 0o111) # chmod a+x
        self.logger.info("Download script saved to %s", self.downloadScriptFileName)

    def createFolders(self):
        if self.folders:
            self.logger.info("Creating folders and links...")
        for folder in self.folders:
            dirName = self.createDir(*folder[0])
            for vID in folder[1:]:
                linkName = self.videos[vID][0]
                fileName = join(dirName, linkName)
                if not lexists(fileName):
                    try:
                        symlink(join('..', linkName), fileName)
                    except Exception, e:
                        self.logger.warning("Can't create link at %s: %s", fileName, e)
                        self.errors += 1

    def run(self):
        try:
            self.logger.info("Starting %s..." % self.driverName)
            self.driver = self.driverClass() # ToDo: Provide parameters to the driver
            if self.credentials:
                self.login(*self.credentials)
            if not self.loggedIn and not self.startURL:
                raise ValueError("Loging failed and no start URL is specified, aborting")
            self.getItemsFromURL(self.startURL)
            if self.vIDs:
                self.processVideos()
                self.createFiles()
                self.createFolders()
        except Exception, e:
            self.logger.error(e)
            self.errors += 1
        finally:
            if self.driver:
                self.driver.close()
        self.logger.info("Crawling complete" + (' with %d errors' % self.errors if self.errors else ''))
        if self.go and self.downloadScriptFileName:
            try:
                self.logger.info("Running download script...")
                subprocess = Popen(abspath(self.downloadScriptFileName), cwd = self.targetDirectory, shell = True)
                subprocess.communicate()
                self.logger.info("Done with code %d", subprocess.returncode)
            except Exception, e:
                self.logger.error("FAILED: %s", e)
        return self.errors

def main(args):
    exit(1 if VimeoDownloader(args).run() else 0)

if __name__ == '__main__':
    main(argv[1:])
