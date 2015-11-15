#!/usr/bin/python
from getopt import getopt
from itertools import count
from logging import getLogger, FileHandler, StreamHandler, Filter, Formatter, DEBUG, INFO, WARNING
from os import close, fdopen, listdir, makedirs, remove
from os.path import basename, getmtime, getsize, isdir, isfile, join, lexists
from re import compile as reCompile
from subprocess import Popen, PIPE, STDOUT
from sys import argv, exit as sysExit, getfilesystemencoding, platform, stdout
from tempfile import mkstemp
from time import sleep, time
from traceback import format_exc

# Console output encoding and buffering problems fixing
stdout = fdopen(stdout.fileno(), 'w', 0)

try: # Selenium configuration
    import selenium
    if tuple(int(v) for v in selenium.__version__.split('.')) < (2, 45):
        raise ImportError('Selenium version %s < 2.45' % selenium.__version__)
    from selenium import webdriver
    from selenium.common.exceptions import NoSuchElementException, TimeoutException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.expected_conditions import presence_of_element_located, presence_of_all_elements_located
    from selenium.webdriver.support.ui import WebDriverWait
    DRIVERS = dict((v.lower(), (v, getattr(webdriver, v))) for v in vars(webdriver) if v[0].isupper()) # ToDo: Make this list more precise
except ImportError, ex:
    print "%s: %s\nERROR: This software requires Selenium.\nPlease install Selenium v2.45 or later: https://pypi.python.org/pypi/selenium\n" % (ex.__class__.__name__, ex)
    sysExit(-1)

try: # pycurl downloader library, required by urlgrabber
    import pycurl # pylint: disable=W0611
except ImportError, ex:
    print "%s: %s\nERROR: This software requires pycurl.\nPlease install pycurl v7.19.3.1 or later: https://pypi.python.org/pypi/pycurl\n" % (ex.__class__.__name__, ex)
    sysExit(-1)

try: # urlgrabber downloader library, used to download files, requires pycurl
    import urlgrabber
    from urlgrabber.grabber import URLGrabber, URLGrabError
    if tuple(int(v) for v in urlgrabber.__version__.split('.')) < (3, 10):
        if tuple(int(v) for v in urlgrabber.__version__.split('.')) < (3, 9, 1):
            raise ImportError('urlgrabber version %s < 3.9.1' % urlgrabber.__version__)
        else:
            print """
WARNING: You're using urlgrabber 3.9.1 which contains a known error.
Please use urlgrabber 3.10 or later whenever possible,
otherwise (on Windows) patch the urlgrabber source:
Locate the file C:\\Python27\\Lib\\site-packages\\urlgrabber\\grabber.py
and in line 1161 replace
\tself.curl_obj.setopt(pycurl.SSL_VERIFYHOST, opts.ssl_verify_host)
with
\tself.curl_obj.setopt(pycurl.SSL_VERIFYHOST, 0)
See https://ask.fedoraproject.org/en/question/35874/yum-pycurl-error-43/ for details.
"""
except ImportError, ex:
    print "%s: %s\nERROR: This software requires urlgrabber.\nPlease install urlgrabber v3.9.1, preferably 3.10 or later: https://pypi.python.org/pypi/urlgrabber\n" % (ex.__class__.__name__, ex)
    sysExit(-1)

try: # Requests HTTP library, used to get remote file size
    import requests
    if tuple(int(v) for v in requests.__version__.split('.')) < (2, 3, 0):
        raise ImportError('Requests version %s < 2.3.0' % requests.__version__)
except ImportError, ex:
    requests = None
    print "%s: %s\nWARNING: Video size information will not be available.\nPlease install Requests v2.3.0 or later: https://pypi.python.org/pypi/requests\n" % (ex.__class__.__name__, ex)

try: # Filesystem symbolic links configuration
    from os import link as hardlink, symlink # UNIX # pylint: disable=E0611
except ImportError:
    try:
        from ctypes import windll # Windows
        dll = windll.LoadLibrary('kernel32.dll')
        def hardlink(source, linkName):
            if not dll.CreateHardLinkW(linkName, source, None):
                raise OSError("code %d" % dll.GetLastError())
        def symlink(source, linkName):
            if not dll.CreateSymbolicLinkW(linkName, source, 0):
                raise OSError("code %d" % dll.GetLastError())
    except Exception, ex:
        hardlink = symlink = None
        print "%s: %s\nWARNING: Filesystem links will not be available.\nPlease run on UNIX or Windows Vista or later.\n" % (ex.__class__.__name__, ex)

isWindows = platform.lower().startswith('win')

TITLE = 'VimeoCrawler v1.95 (c) 2013-2015 Vasily Zakharov vmzakhar@gmail.com'

OPTION_NAMES = ('directory', 'login', 'max-items', 'retries', 'pause', 'set-language', 'embed-preset', 'timeout', 'webdriver')
FIELD_NAMES = ('targetDirectory', 'credentials', 'maxItems', 'retryCount', 'pause', 'setLanguage', 'setPreset', 'timeout', 'driverName')
SHORT_OPTIONS = ''.join(('%c:' % option[0]) for option in OPTION_NAMES) + 'hvunfzcxo'
LONG_OPTIONS = tuple(('%s=' % option) for option in OPTION_NAMES) + ('help', 'verbose', 'update', 'no-download', 'no-folders', 'no-filesize', 'verify-content', 'verify-existing', 'detect-obsolete', 'hard-links', 'hd')
OPTION_PATTERNS = tuple(reCompile(pattern) for pattern in (r'-([^-\s])', r'--(\S+)'))

USAGE_INFO = '''Usage: python VimeoCrawler.py [options] [startURL|videoID videoID ...]

The crawler checks the specified URL and processes the specified videos,
album, channel or the whole account, trying to locate the highest available
quality file for each video.

For every video found a file is downloaded to the target directory.
For any channel or album encountered, a subfolder is created in the target
directory, with symbolic links to the files in the target directory.

In the default configuration, the software requires Mozilla Firefox installed.

Options:
-h --help - Displays this help message.
-v --verbose - Provide verbose logging.
-n --no-download - Crawl only, do not download anything.
-u --update - Process only newer videos that were not fully processed before.
-f --no-folders - Do not create subfolders with links for channels and albums.
-z --no-filesize - Do not get file sizes for videos (speeds up crawling a bit).
   --hard-links - Use hard links instead of symbolic links in subfolders.

-l --login - Vimeo login credentials, formatted as email:password.
-d --directory - Target directory to save all the output files to, default is the current directory.

-w --webdriver - Selenium WebDriver to use for crawling, default is Firefox.
-t --timeout - Download attempt timeout, default is 3 seconds.
-r --retries - Number of page download retry attempts, default is 3.
-p --pause - Pause (in seconds) between video retrievals (helps avoid reCAPTCHA).
-m --max-items - Maximum number of items (videos or folders) to retrieve from one page (usable for testing), default is none.
-s --set-language - Try to set the specified language on all crawled videos.
-e --embed-preset - Try to set the specified embed preset on all crawled videos.
   --hd - Try to set all crawled videos to embed as HD.
-c --verify-content - Verify downloaded files to be valid video files, requires ffmpeg to be available in the path.
-x --verify-existing - Verify already downloaded files to be valid video files, requires ffmpeg to be available in the path.
-o --detect-obsolete - Report existing downloaded files not checked during the run.

If start URL is not specified, the login credentials have to be specified.
In that case, the whole account for those credentials would be crawled.
'''

def usage(error = None):
    '''Prints usage information (preceded by optional error message) and exits with code 2.'''
    print "%s\n" % TITLE
    print USAGE_INFO
    if error:
        print error
    sysExit(2 if error else 0)

LOG_FILE_NAME = 'VimeoCrawler.log'

VIMEO = 'vimeo.com'
VIMEO_URL = 'https://%s/%%s' % VIMEO

HTTP_ERROR_PATTERN = reCompile(r'.*"(.*)"$')

SYSTEM_LINKS = ('about', 'blog', 'categories', 'channels', 'cookie_policy', 'couchmode', 'creativecommons', 'creatorservices', 'dmca', 'enhancer', 'everywhere', 'explore', 'groups', 'help', 'jobs', 'join', 'log_in', 'love', 'musicstore', 'ondemand', 'plus', 'privacy', 'pro', 'robots.txt', 'search', 'site_map', 'staffpicks', 'terms', 'upload', 'videoschool') # http://vimeo.com/link
CATEGORIES_LINKS = ('albums', 'groups', 'channels') # http://vimeo.com/account/category
VIDEOS_LINKS = ('videos') # http://vimeo.com/account/videos
FOLDERS_LINKS = ('album', 'groups', 'channels') # http://vimeo.com/folder/*
FOLDER_NAMES = {'albums': 'album', 'groups': 'group', 'channels': 'channel'} # Mapping to singular for printing
FILE_PREFERENCES = ('Original', '1080p', '720p', 'On2 HD', 'HD', 'On2 SD', 'SD') # Vimeo file versions names

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

INVALID_FILENAME_CHARS = '<>:"/\\|?*\'' # for file names, to be replaced with _
def cleanupFileName(fileName):
    return ''.join('_' if c in INVALID_FILENAME_CHARS else c for c in fileName)

CONSOLE_ENCODING = stdout.encoding or ('cp866' if isWindows else 'UTF-8')
def encodeForConsole(s):
    return s.encode(CONSOLE_ENCODING, 'replace')

FILE_SYSTEM_ENCODING = getfilesystemencoding()
def encodeForFileSystem(s):
    return s.encode(FILE_SYSTEM_ENCODING, 'replace')

def getFileSize(fileName):
    try:
        return getsize(fileName)
    except Exception:
        return None

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
        self.vID        = int(tokens[0]) if self.isVideo else None
        self.account    = tokens[0] if self.isAccount or self.isCategory or self.isVideos else None
        self.category   = tokens[1] if self.isCategory or self.isVideos else None
        self.folder     = tokens[0] if self.isFolder else None
        self.name       = tokens[1] if self.isFolder else self.account if self.isAccount else self.category if self.isCategory or self.isVideos else None
        if self.isFolder and self.folder != 'album' and not self.url.endswith('videos'):
            self.url += '/videos'

    def createFile(self, directory):
        if not self.isVideo:
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

class VimeoCrawler(object):
    def __init__(self, args):
        # Simple options
        self.verbose = False
        self.updateOnly = False
        self.doDownload = True
        self.foldersNeeded = True
        self.getFileSizes = bool(requests)
        self.useHardLinks = False
        self.setHD = False
        self.verifyContent = False
        self.verifyExisting = False
        self.detectObsolete = False
        # Selenium WebDriver settings
        self.driver = None
        self.driverName = 'Firefox'
        self.driverClass = None
        # Options with parameters
        self.credentials = None
        self.targetDirectory = '.'
        self.timeout = 3
        self.retryCount = 3
        self.pause = None
        self.maxItems = None
        self.setLanguage = None
        self.setPreset = None
        # Startup defaults
        self.startURL = None
        self.errors = 0
        self.vIDs = []
        try:
            # Reading command line options
            (options, parameters) = getopt(args, SHORT_OPTIONS, LONG_OPTIONS)
            for (option, value) in options:
                if option in ('-h', '--help'):
                    usage()
                elif option in ('-v', '--verbose'):
                    self.verbose = True
                elif option in ('-u', '--update'):
                    self.updateOnly = True
                elif option in ('-n', '--no-download'):
                    self.doDownload = False
                elif option in ('-f', '--no-folders'):
                    self.foldersNeeded = False
                elif option in ('-z', '--no-filesize'):
                    self.getFileSizes = False
                elif option in ('-c', '--verify-content'):
                    self.verifyContent = True
                elif option in ('-x', '--verify-existing'):
                    self.verifyExisting = True
                elif option in ('-o', '--detect-obsolete'):
                    self.detectObsolete = True
                elif option in ('--hard-links',):
                    self.useHardLinks = True
                elif option in ('--hd',):
                    self.setHD = True
                else: # Parsing options with arguments
                    index = None
                    for (maskNum, pattern) in enumerate(OPTION_PATTERNS):
                        m = pattern.match(option)
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
            else:
                self.setLanguage = self.setPreset = self.setHD = None
            if self.maxItems:
                try:
                    self.maxItems = int(self.maxItems)
                    if self.maxItems < 0:
                        raise ValueError
                except ValueError:
                    raise ValueError("-m / --max-items parameter must be a non-negative integer")
            try:
                self.timeout = int(self.timeout)
                if self.timeout < 0:
                    raise ValueError
            except ValueError:
                raise ValueError("-t / --timeout parameter must be a non-negative integer")
            try:
                self.retryCount = int(self.retryCount)
                if self.retryCount < 0:
                    raise ValueError
            except ValueError:
                raise ValueError("-r / --retries parameter must be a non-negative integer")
            try:
                self.pause = int(self.pause or 0)
                if self.pause < 0:
                    raise ValueError
            except ValueError:
                raise ValueError("-p / --pause parameter must be a non-negative integer")
            if self.setLanguage:
                self.setLanguage = self.setLanguage.capitalize()
            if parameters:
                try:
                    for vID in (URL(p).vID for p in parameters):
                        if vID is None: # At least one parameter is not video
                            raise ValueError
                        if vID not in self.vIDs:
                            self.vIDs.append(vID) # Remove duplicates
                    self.detectObsolete = False
                except ValueError:
                    if len(parameters) == 1:
                        self.startURL = URL(parameters[0])
                    else:
                        raise ValueError("If multiple parameters are specified, they must all be videos")
            elif not self.credentials:
                raise ValueError("Neither login credentials nor start URL is specified")
            else:
                self.vIDs = []
            # Creating target directory
            self.createDir()
            if self.startURL:
                self.startURL.createFile(self.targetDirectory)
            # Configuring logging
            rootLogger = getLogger()
            if not rootLogger.handlers:
                formatter = Formatter("%(asctime)s %(levelname)7s %(message)s", '%Y-%m-%d %H:%M:%S')
                streamHandler = StreamHandler()
                streamHandler.setFormatter(formatter)
                fileHandler = FileHandler(join(self.targetDirectory, LOG_FILE_NAME))
                fileHandler.setFormatter(formatter)
                (f, self.errorLogFileName) = mkstemp('.log', 'VimeoCrawlerErrors-')
                close(f)
                self.errorHandler = FileHandler(self.errorLogFileName)
                self.errorHandler.setFormatter(Formatter("%(operation)s  %(levelname)5s %(message)s", '%Y-%m-%d %H:%M:%S'))
                self.errorHandler.setLevel(WARNING)
                class OperationFilter(Filter):
                    def __init__(self):
                        super(OperationFilter, self).__init__()
                        self.operation = None
                        self.previousOperation = None
                    def filter(self, record):
                        record.operation = ('%s\n' % self.operation) if self.operation != self.previousOperation else ''
                        self.previousOperation = self.operation
                        return True
                self.operationFilter = OperationFilter()
                self.errorHandler.addFilter(self.operationFilter)
                rootLogger.addHandler(streamHandler)
                rootLogger.addHandler(fileHandler)
                rootLogger.addHandler(self.errorHandler)
            self.logger = getLogger('vimeo')
            self.logger.setLevel(DEBUG if self.verbose else INFO)
            self.logger.info("")
            self.logger.info(TITLE)
            if self.verifyContent or self.verifyExisting:
                self.logger.debug("Enabling content verification, checking for ffmpeg...")
                self.setOperation("Enabling content verification, checking for ffmpeg...")
                subprocess = Popen('ffmpeg', shell = True, stdout = PIPE, stderr = STDOUT)
                subprocess.communicate()
                if subprocess.returncode not in (0, 1):
                    self.error("ffmpeg check FAILED (code %d), content verification NOT enabled", subprocess.returncode)
                    self.verifyContent = self.verifyExisting = False
                else:
                    self.logger.debug("OK")
        except Exception, e:
            usage("ERROR: %s" % e)

    def setOperation(self, operation):
        self.operationFilter.operation = operation

    def error(self, *args, **kwargs):
        self.errors += 1
        self.logger.error(*args, **kwargs)

    def createDir(self, dirName = None):
        dirName = join(self.targetDirectory, dirName) if dirName else self.targetDirectory
        if dirName and not isdir(dirName):
            makedirs(dirName)
        return dirName

    def goTo(self, url):
        url = URL(url)
        self.logger.debug("Going to %s", url)
        self.setOperation(str(url))
        self.driver.get(url.url)
        try:
            self.getElement("#topnav_desktop") # Detect if this is a Vimeo page
        except NoSuchElementException:
            try: # Trying to overcome Google reCAPTCHA. To test, use self.driver.get('http://www.google.com/recaptcha/api2/demo')
                self.getElement(".g-recaptcha")
                self.driver.switch_to_frame(self.getElement('iframe'))
                checkMark = self.getElement('.recaptcha-checkbox-checkmark')
                checkMark.click()
                self.driver.switch_to_default_content()
                first = True
                while True:
                    try:
                        self.getElement("#topnav_desktop") # Detect if this is a Vimeo page
                        break
                    except NoSuchElementException:
                        if first:
                            self.logger.info("Hit reCAPTCHA, user input required")
                            first = False
            except NoSuchElementException:
                self.logger.error("Unindentified page, retrying")
                self.driver.get(url.url)

    def getElement(self, selector, wait = False, multiple = False):
        if wait:
            try:
                condition = presence_of_all_elements_located if multiple else presence_of_element_located
                return WebDriverWait(self.driver, self.timeout).until(condition((By.CSS_SELECTOR, selector)))
            except TimeoutException:
                pass # The finder below would create and throw NoSuchElementException to be printed nicely later
        finder = self.driver.find_elements_by_css_selector if multiple else self.driver.find_element_by_css_selector
        return finder(selector)

    def getElements(self, selector, wait = False):
        return self.getElement(selector, wait = wait, multiple = True)

    def login(self, email, password):
        self.goTo('http://vimeo.com/log_in')
        self.logger.info("Logging in as %s...", email)
        try:
            emailInput = self.getElement('#signup_email')
            emailInput.send_keys(email)
            passwordInput = self.getElement('#login_password')
            passwordInput.send_keys(password)
            submitButton = self.getElement('#login_form input[type=submit]')
            submitButton.click()
            welcomeLink = self.getElement('#page_header h1 a', wait = True)
            userName = welcomeLink.text.strip()
            self.logger.info("Logged in as %s...", userName)
            welcomeLink.click()
            self.getElement('#content')
            self.loggedIn = True
            self.userName = userName
            return
        except NoSuchElementException, e:
            self.error("Login failed: %s", e)

    def getItemsFromPage(self):
        self.logger.debug("Processing %s", self.driver.current_url)
        self.setOperation(self.driver.current_url)
        try:
            links = self.getElements('#browse_content .browse a')
            links = (link.get_attribute('href') for link in links)
            items = tuple(URL(link) for link in links if VIMEO in link and not link.endswith('settings'))[:self.maxItems]
        except NoSuchElementException, e:
            self.error(e.msg)
            items = ()
        numVideos = len(tuple(item for item in items if item.isVideo))
        if numVideos:
            if numVideos == len(items):
                self.logger.debug("Got %d videos", numVideos)
            else:
                self.logger.debug("Got %d videos and %d other items", numVideos, len(items) - numVideos)
        else:
            self.logger.debug("Got %d items", len(items))
        assert len(items) == len(set(items))
        return items

    def getItemsFromFolder(self):
        items = []
        numPages = 0
        for _ in xrange(self.maxItems) if self.maxItems != None else count():
            items.extend(self.getItemsFromPage())
            numPages += 1
            try:
                nextButton = self.getElement('.pagination a[rel=next]')
                nextButton.click()
            except NoSuchElementException:
                break
        items = tuple(items)
        assert len(items) == len(set(items))
        if numPages > 1:
            self.logger.debug("Got total of %d items", len(items))
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
            self.logger.info("Processing account %s...", url.account)
            items = tuple(url.url + suffix for suffix in ('/videos', '/channels', '/albums'))
            self.doCreateFolders = self.foldersNeeded
        elif url.isVideos: # Videos
            self.goTo(url)
            self.logger.info("Processing videos...")
            items = self.getItemsFromFolder()
        elif url.isCategory: # Category
            self.goTo(url)
            self.logger.info("Processing %s...", url.category)
            items = self.getItemsFromFolder()
            self.doCreateFolders = self.foldersNeeded
        elif url.isFolder: # Folder
            title = None
            self.goTo(url)
            try:
                title = self.getElement('#page_header h1 a').text # https://vimeo.com/channels/*/videos
            except NoSuchElementException:
                try:
                    title = self.getElement('#page_header h1').text # https://vimeo.com/album/*
                except NoSuchElementException:
                    try:
                        title = self.getElement('#group_header h1 a').get_attribute('title') # https://vimeo.com/groups/*/videos
                    except NoSuchElementException:
                        try:
                            title = self.getElement('#group_header h1 a').text # backup
                        except NoSuchElementException, e:
                            self.logger.error(e.msg)
            if title:
                self.logger.info("Processing folder %s", encodeForConsole(title))
                self.setOperation(encodeForConsole(title))
                if self.doCreateFolders:
                    dirName = self.createDir(cleanupFileName(title.strip().rstrip('.'))) # unicode
                    url.createFile(dirName)
                    if symlink:
                        target = set()
                        self.folders.append((dirName, target))
                items = self.getItemsFromFolder()
        else: # Some other page
            self.goTo(url)
            self.logger.info("Processing page %s...", url.url)
            items = self.getItemsFromPage()
        for item in items:
            self.getItemsFromURL(item, target)

    def verifyFile(self, fileName):
        self.logger.debug("Verifying...")
        subprocess = Popen('ffmpeg -v error -i "%s" -f null -' % fileName, shell = True, stdout = PIPE, stderr = STDOUT)
        output = subprocess.communicate()[0]
        if subprocess.returncode:
            self.error("Verification failed, code %d", subprocess.returncode)
            return False
        if output:
            self.logger.warning("Verification issue: %s", '\n'.join([s for s in output.splitlines() if "Last message repeated" not in s][-4:]))
            return False
        return True

    def processVideo(self, vID, number):
        title = ''
        download = None
        isPrivate = None
        try:
            self.goTo(vID)
            try:
                titleElement = self.getElement('#page_header .video_meta h1') # Legacy style video page
                legacyStyle = True
            except NoSuchElementException, e:
                titleElement = self.getElement('h1.clip_info-header span:not([title])') # New style video page
                legacyStyle = False
            title = encodeForConsole(titleElement.text.strip().rstrip('.'))
            try: # Check if video is private
                isPrivate = self.getElement('.private' if legacyStyle else 'h1.clip_info-header span[title="Password Protected"]')
            except NoSuchElementException:
                pass
            try:
                author = self.getElement('#page_header .byline a[rel=author]' if legacyStyle else 'a.js-user_link').text.strip()
                author = None if author == self.userName else encodeForConsole(author)
            except NoSuchElementException:
                self.error("Failed to identify author")
                author = None
            try:
                downloadButton = self.getElement('.iconify_down_b') if legacyStyle else self.driver.find_element_by_xpath('//button//span[.="Download"]')
                downloadButton.click()
                download = self.getElement('#download' if legacyStyle else "#download_panel")
            except NoSuchElementException, e:
                pass
        except NoSuchElementException, e:
            self.error(e.msg)
            return
        # Parse download links
        link = linkTitle = linkSize = localSize = downloadOK = downloadSkip = None
        if download:
            xpath = './/a[%s]' if legacyStyle else './/td[%s]'
            for preference in FILE_PREFERENCES:
                try: # exact match
                    linkTitle = download.find_element_by_xpath(xpath % ('.="%s"' % preference))
                except NoSuchElementException:
                    try: # contains match
                        linkTitle = download.find_element_by_xpath(xpath % ('contains(., "%s")' % preference))
                    except NoSuchElementException:
                        pass
                if linkTitle:
                    link = linkTitle if legacyStyle else linkTitle.find_element_by_xpath('./following-sibling::td/a[.="Download"]')
                    break
        if link: # Parse chosen download link
            userAgent = str(self.driver.execute_script('return window.navigator.userAgent'))
            cookies = self.driver.get_cookies()
            extension = link.get_attribute('download').split('.')[-1] # unicode
            description = encodeForConsole('%s/%s' % (linkTitle.text, extension.upper()))
            link = str(link.get_attribute('href'))
            if self.getFileSizes:
                try:
                    request = requests.get(link, stream = True, headers = {'user-agent': userAgent}, cookies = dict((str(cookie['name']), str(cookie['value'])) for cookie in cookies))
                    request.close()
                    linkSize = int(request.headers['content-length'])
                    self.totalFileSize += linkSize
                    description += ', %s' % readableSize(linkSize)
                except Exception, e:
                    self.error("Error getting remote file size: %s", e)
        else:
            description = extension = 'NONE'
        # Prepare file information
        operation = '%d %s%s (%s) %d/%d %d%%%s' % (vID, title, ' [P]' if isPrivate else (' [%s]' % author) if author else '', description, number, len(self.vIDs), int(number * 100.0 / len(self.vIDs)), (' %s' % readableSize(self.totalFileSize)) if self.totalFileSize else '')
        self.logger.info(operation)
        self.setOperation(operation)
        if not legacyStyle:
            self.logger.debug("New style video page detected!")
        fileName = cleanupFileName('%s.%s' % (' '.join(((title.decode(CONSOLE_ENCODING),) if title else ()) + (str(vID),)), extension.lower())) # unicode
        targetFileName = encodeForFileSystem(join(self.targetDirectory, fileName))
        if self.setLanguage or self.setPreset or self.setHD:
            if author:
                self.logger.warning("Different video author, skipping settings")
            else: # Matching author or unindentified author
                try:
                    if not legacyStyle:
                        try: # Close download panel
                            closeButton = self.getElement('.modal-btn--close')
                            closeButton.click()
                        except:
                            pass
                    settingsButton = self.getElement('#change_settings') if legacyStyle else self.driver.find_element_by_xpath('//button//span[.="Settings"]')
                    settingsButton.click()
                    if self.setLanguage:
                        try:
                            languages = self.getElements('select[name=language] option')
                            currentLanguage = ([l for l in languages if l.is_selected()] or [None,])[0]
                            if currentLanguage is None or currentLanguage is languages[0]:
                                ls = [l for l in languages if l.text.capitalize().startswith(self.setLanguage)]
                                if len(ls) != 1:
                                    ls = [l for l in languages if l.get_attribute('value').capitalize().startswith(self.setLanguage)]
                                if len(ls) == 1:
                                    self.updateCompleted = False
                                    self.logger.debug("Language not set, setting to %s", ls[0].text)
                                    ls[0].click()
                                    submitButton = self.getElement('#settings_form input[type=submit]')
                                    submitButton.click()
                                else:
                                    self.error("Unsupported language: %s", self.setLanguage)
                                    self.setLanguage = None
                            else:
                                self.logger.debug("Language is already set to %s / %s", currentLanguage.get_attribute('value').upper(), currentLanguage.text)
                        except NoSuchElementException:
                            self.error("Failed to set language to %s", self.setLanguage)
                    if self.setHD:
                        try:
                            videoFileTab = self.getElement('#tabs a[title="Video File"]')
                            videoFileTab.click()
                            try:
                                radio = self.getElement('#hd_profile_1080')
                                if radio.is_selected():
                                    self.logger.debug("Video already set to 1080p")
                                elif not radio.is_enabled():
                                    self.logger.debug("Video cannot be set to 1080p")
                                else:
                                    self.updateCompleted = False
                                    self.logger.debug("Setting video to 1080p")
                                    radio.click()
                                    upgradeVideoButton = self.getElement('#upgrade_video')
                                    upgradeVideoButton.click()
                            except NoSuchElementException:
                                self.error("Failed to set video to 1080p")
                        except NoSuchElementException:
                            self.error("Failed to access Video File settings")
                    if self.setPreset or self.setHD:
                        try:
                            embedTab = self.getElement('#tabs a[title=Embed]')
                            embedTab.click()
                            if self.setHD:
                                try:
                                    checkbox = self.getElement('input[name=allow_hd_embed]')
                                    if checkbox.is_selected():
                                        self.logger.debug("Embed already set to HD")
                                    else:
                                        self.updateCompleted = False
                                        self.logger.debug("Setting embed to HD")
                                        checkbox.click()
                                        saveEmbedSettingsButton = self.getElement('#settings_form input[name=save_embed_settings]')
                                        saveEmbedSettingsButton.click()
                                except NoSuchElementException:
                                    self.error("Failed to set playback to HD")
                            if self.setPreset:
                                try:
                                    presets = self.getElements("select#preset option")
                                    currentPreset = ([p for p in presets if p.is_selected()] or [None,])[0]
                                    if currentPreset and currentPreset.text.capitalize() == self.setPreset:
                                        self.logger.debug("Preset is already set to %s", self.setPreset)
                                    else:
                                        presets = [p for p in presets if p.text.capitalize() == self.setPreset]
                                        if presets:
                                            self.updateCompleted = False
                                            self.logger.debug("Preset %s, setting to %s", ('is set to %s' % currentPreset.text.capitalize()) if currentPreset else 'is not set', self.setPreset)
                                            presets[0].click()
                                            saveEmbedSettingsButton = self.getElement('#settings_form input[name=save_embed_settings]')
                                            saveEmbedSettingsButton.click()
                                        else:
                                            self.error("Unknown preset: %s", self.setPreset)
                                            self.setPreset = None
                                except NoSuchElementException:
                                    self.error("Failed to set preset to %s", self.setPreset)
                        except NoSuchElementException:
                            self.error("Failed to access Embed settings")
                except NoSuchElementException:
                    self.error("Failed to access settings")
        if not download:
            self.logger.warning("Download function not available")
        elif not link:
            self.error("Failed to obtain download link")
        else: # Downloading file
            if linkSize:
                localSize = getFileSize(targetFileName)
                if localSize == linkSize:
                    downloadOK = True
                elif localSize > linkSize:
                    self.updateCompleted = False
                    self.error("Local file is larger (%d) than remote file (%d)", localSize, linkSize)
                    downloadSkip = True
            if downloadOK or downloadSkip:
                if self.verifyExisting and not self.verifyFile(targetFileName):
                    downloadOK = False
            elif self.doDownload:
                timeout = self.timeout
                class ProgressIndicator(object):
                    QUANTUM = 10 * 1024 * 1024 # 10 megabytes
                    ACTION = r'--\\||//' # update() often gets called in pairs, this smoothes things up
                    action = len(ACTION) - 1

                    def progress(self, s, suffix = ''):
                        self.action = (self.action + 1) % len(self.ACTION)
                        stdout.write('\b%s%s' % (s, suffix + '\n' if suffix else self.ACTION[self.action]))

                    def start(self, *_args, **kwargs):
                        self.length = kwargs.get('length') or kwargs.get('size')
                        self.started = False
                        self.totalRead = 0
                        self.lastData = time()
                        self.count = 0
                        self.action = len(self.ACTION) - 1
                        self.progress("Dowloading: ")

                    def update(self, totalRead, suffix = ''):
                        if totalRead == 0:
                            self.started = True
                        elif totalRead <= self.totalRead:
                            if time() > self.lastData + timeout:
                                raise URLGrabError("Download seems stalled")
                        else:
                            self.totalRead = totalRead
                            self.lastData = time()
                        oldCount = self.count
                        self.count = int(totalRead // self.QUANTUM) + 1
                        self.progress(('=' if self.started else '+') * max(0, self.count - oldCount), suffix)
                        self.started = True

                    def end(self, totalRead):
                        self.update(totalRead, 'OK')

                progressIndicator = ProgressIndicator()
                grabber = URLGrabber(reget = 'simple', timeout = self.timeout, progress_obj = progressIndicator,
                    user_agent = userAgent, http_headers = tuple((str(cookie['name']), str(cookie['value'])) for cookie in cookies))
                for _ in xrange(self.retryCount):
                    try:
                        grabber.urlgrab(link, filename = targetFileName)
                        self.updateCompleted = False
                        downloadOK = True
                        break
                    except URLGrabError, e:
                        if e.errno == 14 and e.code == 22:
                            httpError = HTTP_ERROR_PATTERN.match(e.strerror).group(1)
                            if not self.getFileSizes and ' 416 ' in httpError:
                                downloadOK = True
                            else:
                                self.logger.warning("Download failed: %s", httpError)
                        else:
                            self.logger.warning("Download failed: %s", e.strerror if e.errno == 14 else e)
                    except KeyboardInterrupt:
                        self.logger.warning("Download interrupted")
                else:
                    self.updateCompleted = False
                    self.error("Download ultimately failed after %d retries", self.retryCount)
                if downloadOK:
                    localSize = getFileSize(targetFileName)
                    if not localSize:
                        self.updateCompleted = False
                        self.error("Downloaded file seems corrupt")
                        downloadOK = False
                    elif linkSize:
                        if localSize > linkSize:
                            self.updateCompleted = False
                            self.error("Downloaded file larger (%d) than remote file (%d)", localSize, linkSize)
                            downloadOK = False
                        elif localSize < linkSize:
                            self.updateCompleted = False
                            self.error("Downloaded file smaller (%d) than remote file (%d)", localSize, linkSize)
                            downloadOK = False
                        elif self.verifyContent and not self.verifyFile(targetFileName):
                            self.updateCompleted = False
                            downloadOK = False
            if downloadOK:
                self.logger.debug("OK")
            elif downloadSkip or not self.doDownload:
                self.logger.debug("Download SKIPPED")
        # Creating symbolic links, if enabled
        for dirName in (dirName for (dirName, vIDs) in self.folders if vID in vIDs):
            linkFileName = join(dirName, fileName) # unicode
            try:
                if lexists(linkFileName):
                    remove(linkFileName)
            except:
                pass
            try:
                (hardlink if self.useHardLinks else symlink)(join('..', fileName), linkFileName)
            except Exception, e:
                self.error("Can't create link at %s: %s", encodeForConsole(linkFileName), e)

    def checkForObsoletes(self):
        self.logger.info("Checking for obsolete files...")
        files = {}
        folders = set(basename(dirName) for (dirName, _vIDs) in self.folders)
        for fileName in listdir(unicode(self.targetDirectory)):
            fullName = join(self.targetDirectory, fileName)
            if '.' in fileName and isfile(fullName):
                try:
                    vID = int(fileName[fileName.rfind(' ') + 1 : fileName.rfind('.')])
                    files[vID] = files.get(vID, []) + [(fileName, fullName),]
                except ValueError:
                    pass
            elif self.detectObsolete and isdir(fullName) and not fileName in folders:
                self.logger.warning("Unknown folder detected: %s", encodeForConsole(fileName))
        for (vID, fileNames) in files.iteritems():
            assert fileNames
            if self.detectObsolete and vID not in self.vIDs:
                for (fileName, fullName) in fileNames:
                    self.logger.warning("Unknown vID file detected: %s", encodeForConsole(fileName))
                continue
            if len(fileNames) == 1:
                continue
            for (fileName, fullName) in sorted(fileNames, key = lambda (fileName, fullName): (getsize(fullName), getmtime(fullName)))[:-1]:
                self.logger.warning("Duplicate vID file detected: %s", encodeForConsole(fileName))

    def run(self):
        self.doCreateFolders = False
        self.loggedIn = False
        self.userName = None
        self.folders = []
        self.totalFileSize = 0
        try:
            self.logger.info("Starting %s...", self.driverName)
            self.driver = self.driverClass() # ToDo: Provide parameters to the driver
            if self.credentials:
                self.login(*self.credentials)
                if not self.loggedIn:
                    raise ValueError("Aborting")
            if not self.vIDs:
                self.getItemsFromURL(self.startURL)
                if self.folders:
                    self.logger.info("Got total of %d folders", len(self.folders))
                self.vIDs = tuple(sorted(self.vIDs, reverse = True))
            if self.vIDs:
                assert len(self.vIDs) == len(set(self.vIDs))
                self.logger.info("Processing %d videos...", len(self.vIDs))
                if self.getFileSizes:
                    requests.adapters.DEFAULT_RETRIES = self.retryCount
                for (n, vID) in enumerate(self.vIDs, 1):
                    if n > 1 and self.pause:
                        self.logger.debug("Pause %d seconds", self.pause)
                        sleep(self.pause)
                    self.updateCompleted = True
                    self.processVideo(vID, n)
                    if self.updateOnly and self.updateCompleted:
                        self.logger.info("Update completed")
                        break
        except Exception, e:
            self.error(format_exc() if self.verbose else e)
        finally:
            if self.driver:
                self.driver.close()
        self.logger.info("Crawling completed" + (' with %d errors' % self.errors if self.errors else ''))
        self.errorHandler.close()
        getLogger().removeHandler(self.errorHandler)
        with open(self.errorLogFileName) as f:
            summary = f.read().strip()
        try:
            remove(self.errorLogFileName)
        except:
            pass
        if summary:
            (self.logger.error if self.errors else self.logger.warning)("Here's the summary of warnings and errors encountered:\n%s", summary)
        self.checkForObsoletes()
        self.logger.info("Done")
        return self.errors

def main(args):
    sysExit(1 if VimeoCrawler(args).run() else 0)

if __name__ == '__main__':
    main(argv[1:])
