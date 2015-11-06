#!/usr/bin/python
from getopt import getopt
from itertools import count
from logging import getLogger, Formatter, FileHandler, StreamHandler, DEBUG, INFO, WARNING
from re import match
from os import fdopen, listdir, makedirs, remove
from os.path import getmtime, getsize, isdir, isfile, join, lexists
from subprocess import Popen, PIPE, STDOUT
from sys import argv, exit, getfilesystemencoding, platform, stdout # pylint: disable=W0622
from time import time
from traceback import format_exc

# Console output encoding and buffering problems fixing
stdout = fdopen(stdout.fileno(), 'w', 0)

# ToDo: Report non-mentioned videos
# ToDo: Verify already downloaded videos
# ToDo: Do something to Knudepunkt TV problem
# ToDo: Gather all errors to re-display in the end

try: # Selenium configuration
    import selenium
    if tuple(int(v) for v in selenium.__version__.split('.')) < (2, 45):
        raise ImportError('Selenium version %s < 2.45' % selenium.__version__)
    from selenium import webdriver
    from selenium.common.exceptions import NoSuchElementException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.expected_conditions import presence_of_element_located as elementLocated
    from selenium.webdriver.support.ui import WebDriverWait
    DRIVERS = dict((v.lower(), (v, getattr(webdriver, v))) for v in vars(webdriver) if v[0].isupper()) # ToDo: Make this list more precise
except ImportError, ex:
    print "%s: %s\nERROR: This software requires Selenium.\nPlease install Selenium v2.45 or later: https://pypi.python.org/pypi/selenium\n" % (ex.__class__.__name__, ex)
    exit(-1)

try: # pycurl downloader library
    import pycurl # required by urlgrabber # pylint: disable=W0611
except ImportError, ex:
    print "%s: %s\nERROR: This software requires pycurl.\nPlease install pycurl v7.19.3.1 or later: https://pypi.python.org/pypi/pycurl\n" % (ex.__class__.__name__, ex)
    exit(-1)

try: # urlgrabber downloader library, requires pycurl
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
    exit(-1)

try: # Requests HTTP library
    import requests
    if tuple(int(v) for v in requests.__version__.split('.')) < (2, 3, 0):
        raise ImportError('Requests version %s < 2.3.0' % requests.__version__)
except ImportError, ex:
    requests = None
    print "%s: %s\nWARNING: Video size information will not be available.\nPlease install Requests v2.3.0 or later: https://pypi.python.org/pypi/requests\n" % (ex.__class__.__name__, ex)

try: # Filesystem symbolic links configuration
    from os import link as hardlink, symlink # UNIX # pylint: disable=E0611, W0611
except ImportError:
    try:
        from ctypes import windll
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

TITLE = 'VimeoCrawler v1.85 (c) 2013-2015 Vasily Zakharov vmzakhar@gmail.com'

OPTION_NAMES = ('directory', 'login', 'max-items', 'retries', 'set-language', 'preset', 'timeout', 'webdriver')
FIELD_NAMES = ('targetDirectory', 'credentials', 'maxItems', 'retryCount', 'setLanguage', 'setPreset', 'timeout', 'driverName')
SHORT_OPTIONS = ''.join(('%c:' % option[0]) for option in OPTION_NAMES) + 'hvnfzc'
LONG_OPTIONS = tuple(('%s=' % option) for option in OPTION_NAMES) + ('help', 'verbose', 'no-download', 'no-folders', 'no-filesize', 'verify-content', 'hard-links', 'hd')

USAGE_INFO = '''Usage: python VimeoCrawler.py [options] [startURL or videoID]

The crawler checks the specified URL and processes the specified video,
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
-f --no-folders - Do not create subfolders with links for channels and albums.
-z --no-filesize - Do not get file sizes for videos (speeds up crawling a bit).
   --hard-links - Use hard links instead of symbolic links in subfolders.

-l --login - Vimeo login credentials, formatted as email:password.
-d --directory - Target directory to save all the output files to,
                 default is the current directory.

-w --webdriver - Selenium WebDriver to use for crawling, default is Firefox.
-t --timeout - Download attempt timeout, default is 60 seconds.
-r --retries - Number of page download retry attempts, default is 3.
-m --max-items - Maximum number of items (videos or folders) to retrieve
                 from one page (usable for testing), default is none.
-s --set-language - Try to set the specified language on all crawled videos.
-p --preset - Try to set the specified embed preset on all crawled videos.
   --hd - Try to set all crawled videos to embed as HD.
-c --verify-content - Verify downloaded files to be valid video files,
                 requires ffmpeg to be available in the path.

If start URL is not specified, the login credentials have to be specified.
In that case, the whole account for those credentials would be crawled.
'''

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

SYSTEM_LINKS = ('about', 'blog', 'categories', 'channels', 'cookie_policy', 'couchmode', 'creativecommons', 'creatorservices', 'dmca', 'enhancer', 'everywhere', 'explore', 'groups', 'help', 'jobs', 'join', 'log_in', 'love', 'musicstore', 'ondemand', 'plus', 'privacy', 'pro', 'robots.txt', 'search', 'site_map', 'staffpicks', 'terms', 'upload', 'videoschool') # http://vimeo.com/link
CATEGORIES_LINKS = ('albums', 'groups', 'channels') # http://vimeo.com/account/category
VIDEOS_LINKS = ('videos') # http://vimeo.com/account/videos URLs
FOLDERS_LINKS = ('album', 'groups', 'channels') # http://vimeo.com/folder/*
FOLDER_NAMES = {'albums': 'album', 'groups': 'group', 'channels': 'channel'} # Mapping to singular for printing
FILE_PREFERENCES = ('Original', 'On2 HD', 'On2 SD', 'HD', 'SD', 'Mobile', 'file') # Vimeo file versions parts

TIMEOUT = 10

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

class VimeoCrawler(object):
    def __init__(self, args):
        # Simple options
        self.verbose = False
        self.doDownload = True
        self.foldersNeeded = True
        self.getFileSizes = bool(requests)
        self.useHardLinks = False
        self.setPreset = False
        self.setHD = False
        self.verifyContent = False
        # Selenium WebDriver settings
        self.driver = None
        self.driverName = 'Firefox'
        self.driverClass = None
        # Options with parameters
        self.credentials = None
        self.targetDirectory = '.'
        self.timeout = 60
        self.retryCount = 3
        self.maxItems = None
        self.setLanguage = None
        self.startURL = None
        try:
            # Reading command line options
            (options, parameters) = getopt(args, SHORT_OPTIONS, LONG_OPTIONS)
            for (option, value) in options:
                if option in ('-h', '--help'):
                    usage()
                elif option in ('-v', '--verbose'):
                    self.verbose = True
                elif option in ('-n', '--no-download'):
                    self.doDownload = False
                elif option in ('-f', '--no-folders'):
                    self.foldersNeeded = False
                elif option in ('-z', '--no-filesize'):
                    self.getFileSizes = False
                elif option in ('-c', '--verify-content'):
                    self.verifyContent = True
                elif option in ('--hard-links',):
                    self.useHardLinks = True
                elif option in ('--hd',):
                    self.setHD = True
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
            if self.setLanguage:
                self.setLanguage = self.setLanguage.capitalize()
            if len(parameters) > 1:
                raise ValueError("Too many parameters")
            if parameters:
                self.startURL = URL(parameters[0])
            elif not self.credentials:
                raise ValueError("Neither login credentials nor start URL is specified")
            # Creating target directory
            self.createDir()
            if self.startURL:
                self.startURL.createFile(self.targetDirectory)
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
            if self.verifyContent:
                self.logger.info("Enabling content verification, checking for ffmpeg...")
                subprocess = Popen('ffmpeg', shell = True, stdout = PIPE, stderr = STDOUT)
                if subprocess.returncode:
                    self.logger.error("FAILED (code %d), content verification NOT enabled", subprocess.returncode)
                    self.verifyContent = False
                else:
                    self.logger.info("OK")
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

    def getElement(self, selector):
        return self.driver.find_element_by_css_selector(selector)

    def login(self, email, password):
        self.goTo('http://vimeo.com/log_in')
        self.logger.info("Logging in as %s...", email)
        try:
            self.getElement('#signup_email').send_keys(email)
            self.getElement('#login_password').send_keys(password)
            self.getElement('#login_form input[type=submit]').click()
            welcomeLink = WebDriverWait(self.driver, TIMEOUT).until(elementLocated((By.CSS_SELECTOR, '#page_header h1 a')))
            userName = welcomeLink.text.strip()
            self.logger.info("Logged in as %s...", userName)
            welcomeLink.click()
            WebDriverWait(self.driver, TIMEOUT).until(elementLocated((By.CSS_SELECTOR, '#content')))
            self.loggedIn = True
            self.userName = userName
            return
        except NoSuchElementException, e:
            self.logger.error("Login failed: %s", e.msg)

    def getItemsFromPage(self):
        self.logger.info("Processing %s", self.driver.current_url)
        try:
            links = self.driver.find_elements_by_css_selector('#browse_content .browse a')
            links = (link.get_attribute('href') for link in links)
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
        numPages = 0
        for _ in xrange(self.maxItems) if self.maxItems != None else count():
            items.extend(self.getItemsFromPage())
            numPages += 1
            try:
                self.getElement('.pagination a[rel=next]').click()
            except NoSuchElementException:
                break
        items = tuple(items)
        assert len(items) == len(set(items))
        if numPages > 1:
            self.logger.info("Got total of %d items", len(items))
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
                            self.logger.error("Page load failed")
                            self.errors += 1
            if title:
                self.logger.info("Folder: %s", encodeForConsole(title))
                if self.doCreateFolders:
                    dirName = self.createDir(cleanupFileName(title.strip().rstrip('.'))) # unicode
                    url.createFile(dirName)
                    if symlink:
                        target = set()
                        self.folders.append((dirName, target))
                items = self.getItemsFromFolder()
        else: # Some other page
            self.goTo(url)
            items = self.getItemsFromPage()
        for item in items:
            self.getItemsFromURL(item, target)

    def processVideo(self, vID, number):
        title = ''
        download = None
        try:
            self.goTo(vID)
            title = encodeForConsole(self.getElement('#page_header h1').text.strip().rstrip('.'))
            try:
                self.driver.find_element_by_class_name('iconify_down_b').click()
                download = self.getElement('#download')
            except NoSuchElementException, e:
                self.logger.error("Download function not available")
        except NoSuchElementException, e:
            self.logger.warning(e.msg)
            self.logger.error("Page load failed")
            self.errors += 1
        # Parse download links
        link = linkSize = localSize = downloadOK = downloadSkip = None
        if download:
            for preference in FILE_PREFERENCES:
                try:
                    link = download.find_element_by_partial_link_text(preference)
                    break
                except NoSuchElementException:
                    pass
        if link: # Parse chosen download link
            userAgent = str(self.driver.execute_script('return window.navigator.userAgent'))
            cookies = self.driver.get_cookies()
            extension = link.get_attribute('download').split('.')[-1] # unicode
            description = encodeForConsole('%s/%s' % (link.text, extension.upper()))
            link = str(link.get_attribute('href'))
            if self.getFileSizes:
                try:
                    request = requests.get(link, stream = True, headers = { 'user-agent': userAgent }, cookies = dict((str(cookie['name']), str(cookie['value'])) for cookie in cookies))
                    request.close()
                    linkSize = int(request.headers['content-length'])
                    self.totalFileSize += linkSize
                    description += ', %s' % readableSize(linkSize)
                except Exception, e:
                    self.logger.warning(e)
        else:
            description = extension = 'NONE'
        # Prepare file information
        prefix = ' '.join((title, '(%s)' % description))
        suffix = ' '.join((('%d/%d %d%%' % (number, len(self.vIDs), int(number * 100.0 / len(self.vIDs)))),)
                        + ((readableSize(self.totalFileSize),) if self.totalFileSize else ()))
        self.logger.info(' '.join((prefix, suffix)))
        fileName = cleanupFileName('%s.%s' % (' '.join(((title.decode(CONSOLE_ENCODING),) if title else ()) + (str(vID),)), extension.lower())) # unicode
        targetFileName = encodeForFileSystem(join(self.targetDirectory, fileName))
        if self.setLanguage or self.setPreset or self.setHD:
            try:
                author = self.driver.find_element_by_css_selector('#page_header .byline a[rel=author]').text.strip()
            except NoSuchElementException:
                self.logger.error("Failed to identify author")
                author = None
            if author not in (None, self.userName):
                self.logger.warning("Video author is %s, skipping adusting settings", author)
            else: # matching author or unindentified author
                try:
                    self.driver.find_element_by_id('change_settings').click()
                    if self.setLanguage:
                        try:
                            languages = self.driver.find_elements_by_css_selector('select[name=language] option')
                            currentLanguage = ([l for l in languages if l.is_selected()] or [None,])[0]
                            if currentLanguage is None or currentLanguage is languages[0]:
                                ls = [l for l in languages if l.text.capitalize().startswith(self.setLanguage)]
                                if len(ls) != 1:
                                    ls = [l for l in languages if l.get_attribute('value').capitalize().startswith(self.setLanguage)]
                                if len(ls) == 1:
                                    self.logger.info("Language not set, setting to %s", ls[0].text)
                                    ls[0].click()
                                    self.driver.find_element_by_css_selector('#settings_form input[type=submit]').click()
                                else:
                                    self.logger.error("Unsupported language: %s", self.setLanguage)
                                    self.setLanguage = None
                            else:
                                self.logger.info("Language is already set to %s / %s", currentLanguage.get_attribute('value').upper(), currentLanguage.text)
                        except NoSuchElementException:
                            self.logger.warning("Failed to set language to %s", self.setLanguage)
                    if self.setHD:
                        try:
                            self.driver.find_element_by_css_selector('#tabs a[title="Video File"]').click()
                            try:
                                radio = self.driver.find_element_by_id('hd_profile_1080')
                                if radio.is_selected():
                                    self.logger.info("Video already set to 1080p")
                                elif not radio.is_enabled():
                                    self.logger.info("Video cannot be set to 1080p")
                                else:
                                    self.logger.info("Setting video to 1080p")
                                    radio.click()
                                    self.driver.find_element_by_id('upgrade_video').click()
                            except NoSuchElementException:
                                self.logger.warning("Failed to set video to 1080p")
                        except NoSuchElementException:
                            self.logger.warning("Failed to access Video File settings")
                    if self.setPreset or self.setHD:
                        try:
                            self.driver.find_element_by_css_selector('#tabs a[title=Embed]').click()
                            if self.setHD:
                                try:
                                    checkbox = self.driver.find_element_by_css_selector('input[name=allow_hd_embed]')
                                    if checkbox.is_selected():
                                        self.logger.info("Embed already set to HD")
                                    else:
                                        self.logger.info("Setting embed to HD")
                                        checkbox.click()
                                        self.driver.find_element_by_css_selector('#settings_form input[name=save_embed_settings]').click()
                                except NoSuchElementException:
                                    self.logger.warning("Failed to set playback to HD")
                            if self.setPreset:
                                try:
                                    presets = self.driver.find_elements_by_css_selector("select#preset option")
                                    currentPreset = ([p for p in presets if p.is_selected()] or [None,])[0]
                                    if currentPreset and currentPreset.text.capitalize() == self.setPreset:
                                        self.logger.info("Preset is already set to %s", self.setPreset)
                                    else:
                                        presets = [p for p in presets if p.text.capitalize() == self.setPreset]
                                        if presets:
                                            self.logger.info("Preset %s, setting to %s", ('is set to %s' % currentPreset.text.capitalize()) if currentPreset else 'is not set', self.setPreset)
                                            presets[0].click()
                                            self.driver.find_element_by_css_selector('#settings_form input[name=save_embed_settings]').click()
                                        else:
                                            self.logger.error("Unknown preset: %s", self.setPreset)
                                            self.setPreset = None
                                except NoSuchElementException:
                                    self.logger.warning("Failed to set preset to %s", self.setPreset)
                        except NoSuchElementException:
                            self.logger.warning("Failed to access Embed settings")
                except NoSuchElementException:
                    self.logger.warning("Failed to access settings")
        if link: # Downloading file
            if linkSize:
                localSize = getFileSize(targetFileName)
                if localSize == linkSize:
                    downloadOK = True
                elif localSize > linkSize:
                    self.errors += 1
                    self.logger.error("Local file is larger (%d) than remote file (%d)", localSize, linkSize)
                    downloadSkip = True
                    #remove(targetFileName)
                    #localSize = None
            if self.doDownload and not downloadSkip and not downloadOK:
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
                            if time() > self.lastData + timeout: # pylint: disable=W0640
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
                try:
                    grabber.urlgrab(link, filename = targetFileName)
                    downloadOK = True
                except URLGrabError, e:
                    self.errors += 1
                    self.logger.error("Download failed: %s", e)
                except KeyboardInterrupt:
                    self.errors += 1
                    self.logger.error("Download interrupted")
                if downloadOK:
                    localSize = getFileSize(targetFileName)
                    if not localSize:
                        self.errors += 1
                        downloadOK = False
                        self.logger.error("Downloaded file seems corrupt")
                    elif linkSize:
                        if localSize > linkSize:
                            self.errors += 1
                            downloadOK = False
                            self.logger.error("Downloaded file larger (%d) than remote file (%d)", localSize, linkSize)
                        elif localSize < linkSize:
                            self.errors += 1
                            downloadOK = False
                            self.logger.error("Downloaded file smaller (%d) than remote file (%d)", localSize, linkSize)
                        elif self.verifyContent: # Verifying downloaded file
                            self.logger.info("Verifying...")
                            subprocess = Popen('ffmpeg -v error -i "%s" -f null -' % targetFileName, shell = True, stdout = PIPE, stderr = STDOUT)
                            output = subprocess.communicate()[0]
                            if subprocess.returncode:
                                self.logger.warning("Verification failed, code %d", subprocess.returncode)
                            elif output:
                                self.logger.error("Verification ERROR: %s", '\n'.join([s for s in output.splitlines() if "Last message repeated" not in s][-4:]))
            if downloadOK:
                self.logger.info("OK")
            elif downloadSkip or not self.doDownload:
                self.logger.info("Downloading SKIPPED")
        else:
            self.logger.info("Download ultimately failed")
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
                self.logger.warning("Can't create link at %s: %s", encodeForConsole(linkFileName), e)
                self.errors += 1
        self.logger.info("")

    def removeDuplicates(self):
        self.logger.info("Checking for duplicate files...")
        files = {}
        for fileName in listdir(unicode(self.targetDirectory)):
            if '.' not in fileName:
                continue
            fullName = join(self.targetDirectory, fileName)
            if not isfile(fullName):
                continue
            keyName = str(fileName[fileName.rfind(' ') + 1 : fileName.rfind('.')])
            files[keyName] = files.get(keyName, []) + [(fileName, fullName),]
        for (keyName, fullNames) in files.iteritems():
            assert fullNames
            if len(fullNames) == 1:
                continue
            for (fileName, fullName) in sorted(fullNames, key = lambda (fileName, fullName): (getsize(fullName), getmtime(fullName)))[:-1]:
                self.logger.info("Duplicate detected, suggested removal: %s", encodeForConsole(fileName))
                #remove(fullName)
        self.logger.info("Done")

    def run(self):
        self.doCreateFolders = False
        self.loggedIn = False
        self.userName = None
        self.vIDs = []
        self.folders = []
        self.totalFileSize = 0
        self.errors = 0
        try:
            self.logger.info("Starting %s...", self.driverName)
            self.driver = self.driverClass() # ToDo: Provide parameters to the driver
            if self.credentials:
                self.login(*self.credentials)
                if not self.loggedIn:
                    raise ValueError("Aborting")
            self.getItemsFromURL(self.startURL)
            if self.folders:
                self.logger.info("Got total of %d folders", len(self.folders))
            if self.vIDs:
                assert len(self.vIDs) == len(set(self.vIDs))
                self.logger.info("Processing %d videos...", len(self.vIDs))
                if self.getFileSizes:
                    requests.adapters.DEFAULT_RETRIES = self.retryCount
                for (n, vID) in enumerate(sorted(self.vIDs, reverse = True), 1):
                    self.processVideo(vID, n)
        except Exception, e:
            self.logger.error(format_exc() if self.verbose else e)
            self.errors += 1
        finally:
            if self.driver:
                self.driver.close()
        self.logger.info("Crawling completed" + (' with %d errors' % self.errors if self.errors else ''))
        self.removeDuplicates()
        return self.errors

def main(args):
    exit(1 if VimeoCrawler(args).run() else 0)

if __name__ == '__main__':
    main(argv[1:])
