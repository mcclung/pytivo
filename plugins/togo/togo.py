import cookielib
import logging
import os
import thread
import time
import urllib2
import urlparse
from urllib import quote, unquote
from xml.dom import minidom

from Cheetah.Template import Template

import config
from metadata import tag_data, from_container
from plugin import EncodeUnicode, Plugin

SCRIPTDIR = os.path.dirname(__file__)

CLASS_NAME = 'ToGo'

# Some error/status message templates

MISSING = """<h3>Missing Data.</h3>  <br>
You must set both "tivo_mak" and "togo_path" before using this 
function.<br>
The <a href="/TiVoConnect?Command=%s&Container=%s&TiVo=%s">ToGo</a> page 
will reload in 10 seconds."""

RESET_MSG = """<h3>The pyTivo Server has been soft reset.</h3>  <br>
pyTivo has reloaded the pyTivo.conf file and all changes should now be 
in effect. <br>
The <a href="/TiVoConnect?Command=%s&Container=%s">previous</a> page 
will reload in 3 seconds."""

TRANS_INIT = """<h3>Transfer Initiated.</h3>  <br>
Your selected transfer has been initiated.<br>
The <a href="/TiVoConnect?Command=%s&Container=%s&TiVo=%s">ToGo</a> page 
will reload in 3 seconds."""

TRANS_STOP = """<h3>Transfer Stopped.</h3>  <br>
Your transfer has been stopped.<br>
The <a href="/TiVoConnect?Command=%s&Container=%s&TiVo=%s">ToGo</a> page 
will reload in 3 seconds."""

UNABLE = """<h3>Unable to Connect to TiVo.</h3>  <br>
pyTivo was unable to connect to the TiVo at %s</br>
This most likely caused by an incorrect Media Access Key.  Please return 
to the ToGo page and double check your Media Access Key.<br>
The <a href="/TiVoConnect?Command=NPL&Container=%s">ToGo</a> page will
reload in 20 seconds."""

# Preload the templates
trname = os.path.join(SCRIPTDIR, 'templates', 'redirect.tmpl')
tnname = os.path.join(SCRIPTDIR, 'templates', 'npl.tmpl')
REDIRECT_TEMPLATE = file(trname, 'rb').read()
NPL_TEMPLATE = file(tnname, 'rb').read()

status = {} # Global variable to control download threads
tivo_cache = {} # Cache of TiVo NPL

class ToGo(Plugin):
    CONTENT_TYPE = 'text/html'

    def NPL(self, handler, query):
        shows_per_page = 50 # Change this to alter the number of shows returned
        cname = query['Container'][0].split('/')[0]
        folder = ''
        tivo_mak = config.get_server('tivo_mak')
        togo_path = config.get_server('togo_path')
        for name, data in config.getShares():
            if togo_path == name:
                togo_path = data.get('path')

        if 'TiVo' in query:
            tivoIP = query['TiVo'][0]
            theurl = ('https://' + tivoIP +
                      '/TiVoConnect?Command=QueryContainer&ItemCount=' +
                      str(shows_per_page) + '&Container=/NowPlaying')
            if 'Folder' in query:
                folder += query['Folder'][0]
                theurl += '/' + folder
            if 'AnchorItem' in query:
                theurl += '&AnchorItem=' + quote(query['AnchorItem'][0])
            if 'AnchorOffset' in query:
                theurl += '&AnchorOffset=' + query['AnchorOffset'][0]

            r = urllib2.Request(theurl)
            auth_handler = urllib2.HTTPDigestAuthHandler()
            auth_handler.add_password('TiVo DVR', tivoIP, 'tivo', tivo_mak)
            opener = urllib2.build_opener(auth_handler)
            urllib2.install_opener(opener)

            if (theurl not in tivo_cache or
                (time.time() - tivo_cache[theurl]['thepage_time']) >= 60):
                # if page is not cached or old then retreive it
                try:
                    page = urllib2.urlopen(r)
                except IOError, e:
                    t = Template(REDIRECT_TEMPLATE)
                    t.time = '20'
                    t.url = '/TiVoConnect?Command=NPL&Container=' + quote(cname)
                    t.text = UNABLE % (tivoIP, quote(cname))
                    handler.send_response(200)
                    handler.send_header('Content-Type', 'text/html')
                    handler.end_headers()
                    handler.wfile.write(t)
                    return
                tivo_cache[theurl] = {'thepage': minidom.parse(page),
                                      'thepage_time': time.time()}
                page.close()

            xmldoc = tivo_cache[theurl]['thepage']
            items = xmldoc.getElementsByTagName('Item')
            TotalItems = tag_data(xmldoc, 'Details/TotalItems')
            ItemStart = tag_data(xmldoc, 'ItemStart')
            ItemCount = tag_data(xmldoc, 'ItemCount')
            FirstAnchor = tag_data(items[0], 'Links/Content/Url')

            data = []
            for item in items:
                entry = {}
                entry['ContentType'] = tag_data(item, 'ContentType')
                for tag in ('CopyProtected', 'UniqueId'):
                    value = tag_data(item, tag)
                    if value:
                        entry[tag] = value
                if entry['ContentType'] == 'x-tivo-container/folder':
                    entry['Title'] = tag_data(item, 'Title')
                    entry['TotalItems'] = tag_data(item, 'TotalItems')
                    lc = int(tag_data(item, 'LastChangeDate'), 16)
                    entry['LastChangeDate'] = time.strftime('%b %d, %Y',
                                                            time.localtime(lc))
                else:
                    entry.update(from_container(item))
                    keys = {'Icon': 'Links/CustomIcon/Url',
                            'Url': 'Links/Content/Url',
                            'SourceSize': 'Details/SourceSize',
                            'Duration': 'Details/Duration',
                            'CaptureDate': 'Details/CaptureDate'}
                    for key in keys:
                        value = tag_data(item, keys[key])
                        if value:
                            entry[key] = value

                    entry['SourceSize'] = ( '%.3f GB' %
                        (float(entry['SourceSize']) / (1024 ** 3)) )

                    dur = int(entry['Duration']) / 1000
                    entry['Duration'] = ( '%02d:%02d:%02d' %
                        (dur / 3600, (dur % 3600) / 60, dur % 60) )

                    entry['CaptureDate'] = time.strftime('%b %d, %Y',
                        time.localtime(int(entry['CaptureDate'], 16)))

                data.append(entry)
        else:
            data = []
            tivoIP = ''
            TotalItems = 0
            ItemStart = 0
            ItemCount = 0
            FirstAnchor = ''

        cname = query['Container'][0].split('/')[0]
        t = Template(NPL_TEMPLATE, filter=EncodeUnicode)
        t.quote = quote
        t.folder = folder
        t.status = status
        t.tivo_mak = tivo_mak
        t.togo_path = togo_path
        t.tivos = config.tivos
        t.tivo_names = config.tivo_names
        t.tivoIP = tivoIP
        t.container = cname
        t.data = data
        t.len = len
        t.TotalItems = int(TotalItems)
        t.ItemStart = int(ItemStart)
        t.ItemCount = int(ItemCount)
        t.FirstAnchor = quote(FirstAnchor)
        t.shows_per_page = shows_per_page
        handler.send_response(200)
        handler.send_header('Content-Type', 'text/html')
        handler.end_headers()
        handler.wfile.write(t)

    def get_tivo_file(self, url, mak, togo_path):
        # global status
        cj = cookielib.LWPCookieJar()

        parse_url = urlparse.urlparse(url)

        name = unquote(parse_url[2])[10:].split('.')
        name.insert(-1," - " + unquote(parse_url[4]).split("id=")[1] + ".")
        outfile = os.path.join(togo_path, "".join(name))

        r = urllib2.Request(url)
        auth_handler = urllib2.HTTPDigestAuthHandler()
        auth_handler.add_password('TiVo DVR', parse_url[1], 'tivo', mak)
        opener = urllib2.build_opener(urllib2.HTTPCookieProcessor(cj),
                                      auth_handler)
        urllib2.install_opener(opener)

        try:
            handle = urllib2.urlopen(r)
        except IOError, e:
            status[url]['running'] = False
            status[url]['error'] = e.code
            return

        f = open(outfile, 'wb')
        length = 0
        start_time = time.time()
        try:
            while status[url]['running']:
                output = handle.read(1024000)
                if not output:
                    break
                length += len(output)
                f.write(output)
                now = time.time()
                elapsed = now - start_time
                if elapsed >= 5:
                    status[url]['rate'] = int(length / elapsed) / 1024
                    status[url]['size'] += length
                    length = 0
                    start_time = now
            if status[url]['running']:
                status[url]['finished'] = True
        except Exception, msg:
            logging.getLogger('pyTivo.togo').info(msg)
        status[url]['running'] = False
        handle.close()
        f.close()

    def ToGo(self, handler, query):
        cname = query['Container'][0].split('/')[0]
        tivoIP = query['TiVo'][0]
        tivo_mak = config.get_server('tivo_mak')
        togo_path = config.get_server('togo_path')
        for name, data in config.getShares():
            if togo_path == name:
                togo_path = data.get('path')
        t = Template(REDIRECT_TEMPLATE)
        command = query['Redirect'][0]
        params = (command, quote(cname), tivoIP)
        if tivo_mak and togo_path:
            theurl = query['Url'][0]
            status[theurl] = {'running': True, 'error': '', 'rate': '',
                              'size': 0, 'finished': False}
            thread.start_new_thread(ToGo.get_tivo_file,
                                    (self, theurl, tivo_mak, togo_path))
            t.time = '3'
            t.text = TRANS_INIT % params
        else:
            t.time = '10'
            t.text = MISSING % params
        t.url = ('/TiVoConnect?Command=' + command + '&Container=' +
                 quote(cname) + '&TiVo=' + tivoIP)
        handler.send_response(200)
        handler.send_header('Content-Type', 'text/html')
        handler.end_headers()
        handler.wfile.write(t)

    def ToGoStop(self, handler, query):
        theurl = query['Url'][0]
        status[theurl]['running'] = False

        cname = query['Container'][0].split('/')[0]
        tivoIP = query['TiVo'][0]
        command = query['Redirect'][0]
        t = Template(REDIRECT_TEMPLATE)
        t.time = '3'
        t.url = ('/TiVoConnect?Command=' + command + '&Container=' +
                 quote(cname) + '&TiVo=' + tivoIP)
        t.text = TRANS_STOP % (command, quote(cname), tivoIP)
        handler.send_response(200)
        handler.send_header('Content-Type', 'text/html')
        handler.end_headers()
        handler.wfile.write(t)