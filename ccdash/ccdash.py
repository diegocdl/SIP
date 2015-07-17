#!/usr/bin/python

#   ccdash.py
#
#   Copyright (C) 2008 Teluu Inc. (http://www.teluu.com)
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU Lesser General Public License as published
#   by the Free Software Foundation; either version 2, or (at your option)
#   any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU Lesser General Public License for more details
#
#   Author: Benny Prijono <bennylp at pjsip.org>

"""\
ccdash.py provides simple functionality for executing build test and unit
tests and submitting the result to CDash.
"""

# $Id$

import base64
import copy
import glob
import gzip
from optparse import OptionParser,OptionGroup
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import xml.dom
import xml.dom.minidom
import pysvn

PROG = "ccdash-0.2 (r" + "$Rev$".strip("$ ").replace("Rev: ", "") + ")"

# Constants
MAXLOG = -1
TEST_TIMEOUT = (30 * 60)

class ExecStatus:
    """\
    This class encapsulates the return status of CCDash.exec_cmd() method.

    """
    cmd = ""
    output = ""
    errmsg = None
    retcode = 127
    def __init__(self, cmd, output, errmsg, retcode):
        self.cmd = cmd
        self.output = output
        self.errmsg = errmsg
        self.retcode = retcode
    def __str__(self):
        if self.errmsg:
            return "Error executing '%s': %s" % (self.cmd, self.errmsg)
        elif self.retcode:
            return "Error executing '%s': program returned %d" % \
                    (self.cmd, self.retcode)
        else:
            return ""
    def error(self):
        """Returns True if the execution has failed
        """
        return self.errmsg or self.retcode


def xml_escape(txt):
        out = ""
        for c in txt:
                if c == '&':
                        out = out + "&amp;"
                elif c == '<':
                        out = out + "&lt;"
                elif c == '>':
                        out = out + "&gt;"
                elif c >= chr(127):
                        out = out + ( "&#%d;" % (ord(c)) )
                else:
                        out = out + c
        return out

class Node:
    """\
    This class provides basic functionality to write XML documents.

    """
    def __init__(self, tag, attrs=None, body=""):
        self.tag = tag
        self.attrs = attrs
        if not self.attrs: self.attrs = {}
        self.body = body
        self.children = []

    def insertNode(self, child):
        self.children.append(child)

    def __str__(self):
        return "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n" + self._encode()

    def _encode(self, indent=""):
        s = indent + "<" + self.tag
        for k, v in self.attrs.items():
            s = s + " %s=\"%s\"" % (k, xml_escape(v) )
        s = s + ">"
        if len(self.children): s = s + "\n"
        for node in self.children:
            s = s + node._encode(indent + "  ")
        s = s + xml_escape(self.body)
        if len(self.children):
            s = s + indent
        s = s + "</" + self.tag + ">\n"
        return s


class MergedTesting:
        """\
        Helper class to merge several <Testing> nodes into one.
        """
        def __init__(self):
                self.start_dt = None
                self.end_dt = None
                self.elapsed_min = 0
                self.test_names = []
                self.test_nodes = []

        def add_node(self, node):
                if node.nodeName != "Testing":
                        sys.stderr.write("Error: MeregdTesting.add_node() expects <Testing> node\n")
                        return 1
                # StartDateTime
                nl = node.getElementsByTagName("StartDateTime")
                if len(nl):
                        n = nl[0]
                        s = str(time.localtime().tm_year) + " " + n.firstChild.nodeValue
                        sdt = time.strptime(s, "%Y %b %d %H:%M %Z")
                        if not self.start_dt or sdt < self.start_dt:
                                self.start_dt = sdt
                # EndDateTime
                nl = node.getElementsByTagName("EndDateTime")
                if len(nl):
                        n = nl[0]
                        s = str(time.localtime().tm_year) + " " + n.firstChild.nodeValue
                        edt = time.strptime(s, "%Y %b %d %H:%M %Z")
                        if not self.end_dt or edt > self.end_dt:
                                self.end_dt = edt
                # ElapsedMinutes
                nl = node.getElementsByTagName("ElapsedMinutes")
                if len(nl):
                        n = nl[0]
                        elm = float(n.firstChild.nodeValue)
                        self.elapsed_min = self.elapsed_min + elm
                # TestList
                nl = node.getElementsByTagName("TestList")
                if len(nl):
                        tl = nl[0]
                        for n in tl.childNodes:
                                if n.nodeType != xml.dom.Node.ELEMENT_NODE:
                                        continue
                                if n.nodeName != "Test":
                                        continue
                                self.test_names.append(n.firstChild.nodeValue)
                # Test
                for n in node.childNodes:
                        if n.nodeType != xml.dom.Node.ELEMENT_NODE or n.nodeName.lower() != "test":
                                continue
                        nn = n.cloneNode(1)
                        self.test_nodes.append(nn)

        def add_to_doc(self, doc):
                # Testing
                testing = doc.createElement("Testing")
                if self.start_dt:
                        sdt = doc.createElement("StartDateTime")
                        sdt_body = doc.createTextNode(time.strftime("%b %d %H:%M GMT", self.start_dt))
                        sdt.appendChild(sdt_body)
                        testing.appendChild(sdt)
                if self.end_dt:
                        edt = doc.createElement("EndDateTime")
                        edt_body = doc.createTextNode(time.strftime("%b %d %H:%M GMT", self.end_dt))
                        edt.appendChild(edt_body)
                        testing.appendChild(edt)
                if True:
                        elm = doc.createElement("ElapsedMinutes")
                        elm_body = doc.createTextNode("%.1f" % (self.elapsed_min))
                        elm.appendChild(elm_body)
                        testing.appendChild(elm)


                # TestList
                tl = doc.createElement("TestList")
                testing.appendChild(tl)
                for name in self.test_names:
                        t = doc.createElement("Test")
                        v = doc.createTextNode(name)
                        t.appendChild(v)
                        tl.appendChild(t)
                # Test
                for tn in self.test_nodes:
                        testing.appendChild(tn)
                doc.documentElement.appendChild(testing)


class CCDash:
    """This class contains basic identifications about the submission,
    program settings, as well as some utility functions.

    """
    def __init__(self, submit_url, site_name, build_name, stamp, wdir):
        self.submit_url = submit_url    # Full submit URL
        self.site_name = site_name      # This site name
        self.build_name = build_name    # The build name
        self.stamp = stamp              # The build stamp
        self.no_upload = False          # Don't upload
        self.xml_out = None             # Save XML output to file
        self.t1 = 0                     # Test timeout
        self.verbosity = 0              # Stdout verbosity:
                                        #  0: print nothing,
                                        #  1: print important info,
                                        #  2: print everything
        self.log_level = 2              # Logging level:
                                        #  0: never submit log
                                        #  1: submit log on error
                                        #  2: always submit log
        self.max_log = MAXLOG           # Maximum number of bytes to send
                                        # -1: no limit
                                        #  N: limit to N bytes
        self.last_log = True            # If max_log is set to N and this flag
                                        # is set send the last N characters
                                        # rather than the first N.
        self.wdir = wdir                # Default working directory

        self.tmp_dir = None
        if sys.platform=="win32":
            self.win32 = True
        else:
            self.win32 = False

    def trace(self, level, msg):
        if level < 0:
            sys.stderr.write(msg + '\n')
        elif level <= self.verbosity:
            sys.stdout.write(msg + '\n')

    def err_exit(self, msg):
        self.trace(-1, msg)
        sys.exit(1)

    def gettime(self):
        return time.time()

    def encodetime(self, t):
        # Note:
        #  we hardcoded timezone to GMT since CDash probably has some
        #  difficulties in parsing it. And since CDash stores timestamps
        #  in GMT anyway.
        return time.strftime("%b %d %H:%M GMT", time.gmtime(t))

    def encodedur(self, elapsed, div=60):
        return "%.3f" % (elapsed/div)

    def tempnam(self, prefix=""):
        #if self.tmp_dir is None:
        #    try:
        #        return os.tempnam()
        #    except:
        #        pass

        if self.tmp_dir is None:
            self.tmp_dir = tempfile.gettempdir()

        randname = "%s%s%d-%d" % ("cc", prefix, os.getpid(),
                                  random.randint(0,999))
        return os.path.join(self.tmp_dir, randname)

    def _terminate_proc(self, proc, fout):
        msg = "*** ccdash timeout: process has been running for "+\
              "too long, attempting to stop it now ***"
        self.trace(-1, "  " + msg)
        if fout:
            fout.flush()
            fout.write("\n" + msg + "\n")
        try:
            proc.terminate()
            msg = "*** process terminated successfully ***"
            self.trace(-1, "  " + msg)
            if fout: fout.write(msg + "\n")
        except Exception, e:
            errmsg = str(e)
            msg = "*** error terminating process: %s ***" % (errmsg)
            self.trace(-1, "  " + msg)
            if fout: fout.write(msg + "\n")

    def exec_cmd(self, cmdline, out_fname=None, ret_output=False,
                 filter_func=None):
        """\
        This is a utility function to execute 'cmdline' and capture the stdout
        and stderr output of the process.

        If 'out_fname' is specified, then the output will be saved to that
        file.

        If 'ret_output" is set to True, the function will return the whole
        output content in the return tuple value.

        The 'filter_func' argument is a function which will be called to
        process each line from the output. The function could be a member
        function or plain function, and it must be declared as:

            filter_func(line_string, line_num)          # For plain function
            filter_func(self, line_string, line_num)    # For member function

        This function returns ExecStatus object.

        The ExecStatus.output will contain empty string if 'ret_output"
        argument is False, otherwise it will contain the whole output.

        The ExecStatus.errmsg will be None if the command was executed,
        otherwise it will contain the error string returned by the OS.

        The ExecStatus.retcode contains the return value of the executed
        process.

        """
        fout = None
        proc = None
        errmsg = ""
        out_str = ""
        is_tmp = False
        if out_fname:
            fout = open(out_fname, "w")
        else:
            out_fname = self.tempnam("exec")
            fout = open(out_fname, "w")
            is_tmp = True
        try:
            self.trace(1, "  executing '%s'.." % (cmdline))
            # Notes:
            #  - we only use shell on non Win32 since the shell in Win32
            #    is CMD.EXE, and we want to be able to run this on mingw.
            proc = subprocess.Popen(cmdline, shell=not self.win32,
                                    stdin=subprocess.PIPE,
                                    stdout=fout, stderr=subprocess.STDOUT,
                                    universal_newlines=False)
        except Exception, e:
            errmsg = str(e)
        except:
            errmsg = str(sys.exc_info()[0])

        if errmsg != "":
            if proc:
                # Only in Python 2.6
                if sys.hexversion >= 0x02060000: proc.terminate()
                else: proc.communicate()
                proc = None
            fout.close()
            try:
                if is_tmp: os.remove(out_fname)
            except:
                pass
            self.trace(-1, "  error: %s" % (errmsg))
            return ExecStatus(cmdline, None, errmsg, 127)

        # Make the process get EOF when reading stdin.
        proc.stdin.close()

        # Spawn timer to kill the process if it's running for too log
        # Only in Python 2.6
        if self.t1 > 0 and sys.hexversion >= 0x02060000:
            timer = threading.Timer(self.t1, self._terminate_proc,
                                    (proc, fout))
            timer.start()
        else:
            timer = None

        # Wait for process to complete
        proc.communicate()

        if timer and timer.is_alive():
            timer.cancel()
            # Join the thread to make sure that output has been flushed to
            # fout before we close it below. Also without this, it's possible
            # that the timer thread is still trying to write something to
            # fout after we close it.
            timer.join()
            timer = None

        # Process output
        fout.close()
        fout = open(out_fname, "r")
        line_num = 0
        while True:
            line_str = fout.readline()
            if line_str=="":
                break
            line_str = line_str.replace("\r", "")
            line_num = line_num + 1
            if ret_output:
                out_str = out_str + line_str
            line_str = line_str.rstrip("\r\n")
            self.trace(2, "   " + line_str)
            if filter_func:
                filter_func(line_str, line_num)

        fout.close()
        if is_tmp:
            try:
                os.remove(out_fname)
            except:
                pass

        return ExecStatus(cmdline, out_str, None, proc.returncode)


    def encode_log(self, success, log, encoding="", compression=""):
        """\
        Encode the specified log messages using the specified encoding and
        compression, taking into account the various output log settings.

        """
        if len(log)==0:
            return ""
        if self.log_level==0:
            return ""
        elif self.log_level==1 and success:
            return ""

        if self.max_log == 0:
            return ""
        elif self.max_log > 0 and len(log) > self.max_log:
            if self.last_log:
                log = "(Output trimmed to last " + str(self.max_log) + \
                       " bytes)\n.."+ log[len(log)-self.max_log:]
            else:
                log = log[:self.max_log] + "..\n" + \
                      "(Output trimmed to the first " + str(self.max_log) + \
                      " bytes)\n"

        if compression.find("gzip") >= 0 or compression.find("gz") >= 0:
            tmpnam = self.tempnam("gz")
            f = gzip.open(tmpnam, "wb")
            f.write(log)
            f.close()
            f = open(tmpnam, "rb")
            log = f.read()
            f.close()
            os.remove(tmpnam)

        if encoding.find("base64") >= 0:
            log = base64.b64encode(log)

        return log

    def merge_files(self, files):
        docs = []
        mrt = MergedTesting()

        # Parse all documents
        for fname in files:
                try:
                        doc = xml.dom.minidom.parse(fname)
                except Exception, e:
                        errmsg = str(e)
                        sys.stderr.write(fname + ": Error: " + errmsg)
                        return 1
                except:
                        errmsg = str(sys.exc_info()[0])
                        sys.stderr.write(fname + ": Error: " + errmsg)
                        return 1

                if doc.documentElement.nodeName.lower() == "site":
                        site = doc.documentElement
                        name = site.getAttribute("Name")
                        if name != self.site_name:
                            sys.stderr.write( "%s: Warning: different site name '%s' (expecting '%s')\n" % (fname, name, self.site_name))
                        osname = site.getAttribute("OSName")
                        if osname != platform.system():
                            sys.stderr.write( "%s: Warning: different OSName '%s' (expecting '%s')\n" % (fname, osname, platform.system()))
                        osrelease = site.getAttribute("OSRelease")
                        if osrelease!= platform.release():
                            sys.stderr.write("%s: Warning: different OSRelease '%s' (expecting '%s')\n" % (fname, osrelease, platform.release()))
                        build_name= site.getAttribute("BuildName")
                        if build_name != self.build_name:
                            sys.stderr.write("%s: Warning: different BuildName '%s' (expecting '%s')\n" % (fname, build_name, self.build_name))
                        stamp = site.getAttribute("BuildStamp")
                        if stamp!= self.stamp:
                            sys.stderr.write("%s: Warning: different BuildStamp '%s' (expecting '%s')\n" % (fname, stamp, self.stamp))

                docs.append(doc)

        # Create <Site> containing cmdline options
        newnode = self.create_node()

        # Create XML document from the Node
        newdoc = xml.dom.minidom.parseString(str(newnode))

        # Append the elements from the parsed document
        for doc in docs:
                # Special treatment for <update>
                if doc.documentElement.nodeName.lower() == "update":
                        newdoc.documentElement.appendChild(doc.documentElement.cloneNode(1))
                else:
                        for node in doc.documentElement.childNodes:
                            if node.nodeType != xml.dom.Node.ELEMENT_NODE:
                                   continue
                            #sys.stdout.write("  node <" + node.nodeName + "> added\n")
                            if node.nodeName.lower() == "testing":
                                    mrt.add_node(node)
                            else:
                                    newdoc.documentElement.appendChild(node.cloneNode(1))

        # Add combined <Testing> node to new doc
        mrt.add_to_doc(newdoc)

        # Upload
        #outstr = newdoc.toprettyxml(indent=" ", encoding='utf-8')
        outstr = newdoc.toxml('utf-8')
        self.upload_xml(outstr)
        return 0

    def encode_log_file(self, success, filename, encoding="", compression=""):
        """\
        gzip the specified filename, encode it in base64, and return the
        content as string.

        Note: base64 and gzip utilities are needed
        """
        f = open(filename, "rb")
        log = f.read()
        f.close()
        return self.encode_log(success, log, encoding, compression)

    def upload_xml(self, data):
        """\
        Encode the XML node in 'doc' and send it with HTTP PUT.

        Note: curl is needed
        """
        if self.xml_out:
            tmp = self.xml_out
            self.trace(1, "  output xml saved to '" + tmp + "'")
        else:
            tmp = self.tempnam("xml")
            self.trace(1, "  temporary output xml saved to '" + tmp + "'")
        f = open(tmp, "w")
        f.write(data)
        f.close()
        if not self.no_upload:
            cmd = "curl -T %s %s" % (tmp, self.submit_url)
            ret = self.exec_cmd(cmd, ret_output=True)
            if ret.output:
                self.trace(2, "   " + ret.output)
            if ret.error():
                if tmp != self.xml_out:
                    os.remove(tmp)
                self.err_exit(str(ret))
        else:
            self.trace(1, "  not uploading (disabled by cmdline)")
        if tmp != self.xml_out:
            os.remove(tmp)

    def create_node(self):
        """\
        Create <Site> XML node.

        """
        return Node("Site",
                    attrs = {"Name": self.site_name,
                             "Generator": PROG,
                             "BuildName": self.build_name,
                             "BuildStamp": self.stamp,
                             "OSName": platform.system(),
                             "OSRelease": platform.release(),
                             "OSVersion": platform.version()})


class BuildMsgItem:
    """\
    This class represents a single build warning or error, which was parsed
    by the Build class from the compiler output.

    """
    def __init__(self, type, log_line_num, text, file, line, \
                 pre="", post="", rep="0"):
        self.type = type
        self.log_line_num = log_line_num
        self.text = text
        self.file = file
        self.line = line
        self.pre = pre
        self.post = post
        self.rep = rep

    def create_xml(self):
        """\
        Create <Warning> or <Error> XML node.

        """
        w = Node(self.type)
        w.insertNode(Node("BuildLogLine", body=str(self.log_line_num)))
        w.insertNode(Node("Text", body=self.text))
        w.insertNode(Node("SourceFile", body=self.file))
        w.insertNode(Node("SourceLineNumber", body=str(self.line)))
        w.insertNode(Node("PreContext", body=str(self.pre)))
        w.insertNode(Node("PostContext", body=str(self.post)))
        w.insertNode(Node("RepeatCount", body=str(self.rep)))
        return w


class Operation:
    """\
    Base class for CCDash operations.
    """
    def execute(self):
        pass

    def create_xml(self):
        return None

    def get_name(self):
        return ""

    def get_info(self):
        return ""

    def success(self):
        sys.stderr.write("Operation.success() is called\n")
        return False

class Build(Operation):
    """\
    This class contains functionalities to perform build operation, parse
    the compiler output, and generate the appropriate XML document for
    CDash submission.

    """
    @classmethod
    def create_from_xml(cls, ci, node):
        """\
        Factory method to create the instance from an xml node.
        Attributes:
            - cmd: mandatory, the full cmdline
            - dir: optional, working directory

        """
        cmd = node.getAttribute("cmd")
        wdir = os.path.join(ci.wdir, node.getAttribute("wdir"))
        if not cmd:
            return None
        obj = cls(ci, cmd, wdir)
        if node.getAttribute("disabled") and node.getAttribute("disabled")!="0":
                obj.disabled = True
        return obj

    def __init__(self, ci, cmd, wdir=None, build_log=False, build_log_file=""):
        self.disabled = False
        self.ci = ci                    # The CCDash instance
        self.cmd = cmd                  # Build cmd
        self.wdir = wdir                # Working directory for build cmd
        self.build_log = build_log      # Include build log in submission
        self.t0 = self.t1 = 0           # Start and end time
        self.log_file = build_log_file  # Build output/log file
        if self.log_file:
            # Need to convert build_log_file to absolute path, otherwise
            # it will be written to build directory
            self.log_file = os.path.abspath(self.log_file)
            self.is_tmp_log = False
        else:
            self.is_tmp_log = True
        self.warn_err = []              # List of warnings and errors
        self.have_err = False           # Does the list have error
        self.prev_line = self.cur_line = self.next_line = None
        self.line_cnt = 0;              # Build line count
        self.winscw_err = re.compile(".*:[0-9]+: ")

    def get_name(self):
        return "build"

    def get_info(self):
        return "build cmd='%s'" % (self.cmd)

    def success(self):
        return not self.have_err

    def _good_filename(self, filename):
        """\
        Remove funny characters in filename produced by VCBUILD.
        """
        pos = filename.find(">")
        if pos >= 0:
            filename = filename[pos+1:]
        return filename

    def _parse_output(self, the_line, line_num):
        """\
        Internal utility function to parse compiler output, encapsulate
        warning and error messages as BuildItem's, and save them to
        a list.

        """
        self.prev_line = self.cur_line
        self.cur_line = self.next_line
        self.next_line = the_line
        self.line_cnt = line_num

        if self.cur_line is None:
            return

        line_str = self.cur_line

        # gcc note format:
        #  path/to/file.c:123: note: the message
        pos = line_str.find("note:")
        if pos > 0:
            # ignore
            return

        # gcc warning format:
        #  path/to/file.c:123: warning: the message
        pos = line_str.find("warning:")
        if pos > 0:
            tokens = line_str.split(':', 2)
            self.warn_err.append(BuildMsgItem("Warning", line_num-1, line_str,
                                              tokens[0], tokens[1],
                                              pre=self.prev_line,
                                              post=self.next_line))
            return
        # gcc error format:
        #  path/to/file.c:123: error: the message
        pos = line_str.find("error:")
        if pos > 0:
            tokens = line_str.split(':', 2)
            self.have_err = True
            self.warn_err.append(BuildMsgItem("Error", line_num-1, line_str,
                                              tokens[0], tokens[1],
                                              pre=self.prev_line,
                                              post=self.next_line))
            return
        # MSVC warning format:
        #  path/to/file(123) : warning Cxxxx: the message
        pos = line_str.find(" : warning ")
        if pos > 0:
            line_str = self._good_filename(line_str)
            p0 = line_str.find("(")
            p1 = line_str.find(")")
            if p0>0 and p0 < pos:
                filename = line_str[0:p0]
                line = line_str[p0+1:p1]
            else:
                filename = line_str[0:pos]
                line = ""
            self.warn_err.append(BuildMsgItem("Warning", line_num-1, line_str,
                                              filename, line,
                                              pre=self.prev_line,
                                              post=self.next_line))
            return
        # MSVC error format:
        #  path/to/file(123) : error Cxxxx: the message
        pos = line_str.find(" : error ")
        if pos > 0:
            line_str = self._good_filename(line_str)
            p0 = line_str.find("(")
            p1 = line_str.find(")")
            if p0>0 and p0 < pos:
                filename = line_str[0:p0]
                line = line_str[p0+1:p1]
            else:
                filename = line_str[0:pos]
                line = ""
            self.have_err = True
            self.warn_err.append(BuildMsgItem("Error", line_num-1, line_str,
                                              filename, line,
                                              pre=self.prev_line,
                                              post=self.next_line))
            return
        # MSVC fatal error format:
        #  SOMETHING : fatal error XXXX: the message
        pos = line_str.find(" : fatal error ")
        if pos > 0:
            line_str = self._good_filename(line_str)
            p0 = line_str.find("(")
            p1 = line_str.find(")")
            if p0>0 and p0 < pos:
                filename = line_str[0:p0]
                line = line_str[p0+1:p1]
            else:
                filename = line_str[0:pos]
                line = ""
            self.have_err = True
            self.warn_err.append(BuildMsgItem("Error", line_num-1, line_str,
                                              filename, line,
                                              pre=self.prev_line,
                                              post=self.next_line))
            return
        # Symbian Winscw compile error format:
        #  path/to/file:1234: the message
        if self.winscw_err.match(line_str):
            p0 = line_str.find(":")
            p1 = line_str.find(":", p0+1)
            if p0>0 and p1>0 and p1-p0<6:
                filename = line_str[0:p0]
                line = line_str[p0+1:p1]
                self.have_err = True
                self.warn_err.append(BuildMsgItem("Error", line_num-1,
                                                  line_str, filename, line,
                                                  pre=self.prev_line,
                                                  post=self.next_line))
    def exit_code(self):
        return int(self.have_err)

    def execute(self):
        """\
        Execute the build command, parse the output, and save the result.

        """
        if self.disabled:
                self.ci.trace(1, "Build operation disabled")
                return

        self.ci.trace(1, "Executing build command: '" + self.cmd + "'..")
        if not self.log_file:
            self.log_file = self.ci.tempnam("build")
        self.t0 = self.t1 = self.ci.gettime()
        if self.wdir:
            cwd = os.getcwd()
            os.chdir(self.wdir)
            self.ci.trace(1, "  setting work directory: chdir " + self.wdir)
        else:
            cwd = None

        ret = self.ci.exec_cmd(self.cmd,
                               out_fname=self.log_file,
                               filter_func=self._parse_output)

        if cwd is not None:
            os.chdir(cwd)

        if ret.error():
            self.have_err = True
            self.warn_err.append(BuildMsgItem("Error", self.line_cnt,
                                              str(ret), "", 0))

        self.t1 = self.ci.gettime()

    def create_xml(self):
        """\
        Create XML node suitable for build submission to CDash.

        """
        if self.disabled:
                return None
        # <Site> node
        s = self.ci.create_node()
        # <Build> node
        b = Node("Build")
        s.insertNode(b)
        b.insertNode(Node("BuildCommand", body=self.cmd))
        b.insertNode(Node("StartDateTime", body=self.ci.encodetime(self.t0)))
        b.insertNode(Node("EndDateTime", body=self.ci.encodetime(self.t1)))
        b.insertNode(Node("ElapsedMinutes",
                          body=self.ci.encodedur(self.t1-self.t0)))
        # Warnings and errors
        for w in self.warn_err:
            b.insertNode(w.create_xml())
        if self.build_log:
            oldmax = self.ci.max_log
            self.ci.max_log = -1
            b.insertNode(Node("Log",
                              attrs={"Encoding": "base64",
                                     "Compression": "/bin/gzip"},
                              body=self.ci.encode_log_file(False,
                                                           self.log_file,
                                                           "base64", "gzip")))
            self.ci.max_log = oldmax

        # Delete tmp file
        if self.is_tmp_log:
            try:
                os.remove(self.log_file)
            except:
                pass
        # Done
        return s


class TestItem(Operation):
    """\
    This class describes a single unit test entry. It contains the command-
    line to execute the test, as well as other properties to capture the
    status of the execution.

    """
    @classmethod
    def create_from_xml(cls, ci, node):
        """\
        Factory method to create the instance from an xml node.
        Attributes:
            - name: mandatory, the test name
            - cmd: mandatory, the full cmdline
            - info: optional, longer description
            - wdir: optional, working directory
            - xmlresult: optional, optional XML result file to pick up,
                         otherwise the test output is captured from
                         stdout/stderr

        """
        name = node.getAttribute("name")
        if not name:
            return None
        wdir = os.path.join(ci.wdir, node.getAttribute("wdir"))
        exe = node.getAttribute("exe")
        if exe:
                cmds = glob.glob(os.path.join(wdir, exe))
                if len(cmds):
                        cmd = cmds[0]
                else:
                        sys.stderr.write("Warning: could not expand test cmd '%s'" % (exe))
                        cmd = exe
        else:
                cmd = node.getAttribute("cmd")
                if not cmd:
                    return None
        info = node.getAttribute("info")
        if not info:
                info = name
        xmlresult = node.getAttribute("xmlresult")
        obj = cls(ci, name, info, cmd, wdir, xmlresult)
        if node.getAttribute("disabled") and node.getAttribute("disabled")!="0":
                obj.disabled = True
        return obj

    def __init__(self, ci, name, info, cmd, wdir=None, xmlresult=""):
        self.disabled = False
        self.ci = ci                    # CCDash instance
        self.name = name                # Name
        self.info = info                # Description (?)
        self.fullname = self.info       # Full name
        self.cmd = cmd                  # Command line
        self.wdir = wdir                # Working directory
        self.t0 = self.t1 = 0           # Start and end time
        self.issuccess = False            # Test result
        self.completion = "Completed"   # Completion status
        self.output = ""                # Full output
        self.xmlresult = xmlresult      # XML result of the test, if any
        self.exit_val = ""
        self.exit_value = ""

    def get_name(self):
        return "testitem"

    def get_info(self):
        return "testitem cmd='%s'" % (self.cmd)

    def success(self):
        return self.issuccess

    def exit_code(self):
        return not self.issuccess

    def execute(self):
        """\
        Execute the test and capture the result.

        """
        if self.disabled:
                self.ci.trace(1, "Test operation disabled")
                return
        self.ci.trace(1, "Executing test command: '" + self.cmd + "'...")
        self.t0 = self.t1 = self.ci.gettime()
        if self.wdir:
            cwd = os.getcwd()
            os.chdir(self.wdir)
            self.ci.trace(2, "  setting workdir: chdir " + self.wdir)
        else:
            cwd = None

        ret = self.ci.exec_cmd(self.cmd, ret_output=True)
        if cwd:
            os.chdir(cwd)
        self.completion = "Completed"
        if ret.error():
            self.issuccess = False
            self.exit_value = str(ret.retcode)
            self.output = str(ret.output) + "\n" + str(ret)
            self.ci.trace(1, "  Test %s failed: %s" % (self.name, str(ret)))
        else:
            self.output = ret.output
            self.issuccess = True
            self.ci.trace(1, "  Test %s success" % (self.name))
        self.t1 = self.ci.gettime()

    def create_xml(self):
        if self.disabled:
                return None
        # <Test>
        if self.issuccess:
            t = Node("Test", attrs={"Status": "passed"})
        else:
            t = Node("Test", attrs={"Status": "failed"})
        t.insertNode(Node("Name", body=self.name))
        t.insertNode(Node("FullName", body=self.fullname))
        if self.wdir:
            t.insertNode(Node("Path", body=self.wdir))
        else:
            t.insertNode(Node("Path", body=os.getcwd()))
        t.insertNode(Node("FullCommandLine", body=self.cmd))
        # <Results>
        r = Node("Results")
        t.insertNode(r)
        # "Execution Time"
        n = Node("NamedMeasurement", attrs={"type": "numeric/double",
                                            "name": "Execution Time"})
        n.insertNode(Node("Value", body=str(self.t1-self.t0)))
        r.insertNode(n)
        # Completion Status
        n = Node("NamedMeasurement", {"type": "text/string",
                                      "name": "Completion Status"})
        n.insertNode(Node("Value", body=self.completion))
        r.insertNode(n)
        # Command Line
        n = Node("NamedMeasurement", {"type": "text/string",
                                      "name": "Command Line"})
        n.insertNode(Node("Value", body=self.cmd))
        r.insertNode(n)
        # Exit Code
        if self.exit_val != "":
            n = Node("NamedMeasurement", {"type": "text/string",
                                          "name": "Exit Code"})
            n.insertNode(Node("Value", body=self.exit_val))
            r.insertNode(n)
        # Exit Value
        if self.exit_value != "":
            n = Node("NamedMeasurement", {"type": "text/string",
                                          "name": "Exit Value"})
            n.insertNode(Node("Value", body=self.exit_value))
            r.insertNode(n)
        # <Measurement>
        m = Node("Measurement")
        m.insertNode(Node("Value", body=self.ci.encode_log(self.issuccess,
                                                           self.output)))
        r.insertNode(m)
        return t



class Test(Operation):
    """\
    This class represents unit tests submission. It contains one or more
    TestItem which describes the individual test.

    """
    def __init__(self, ci, items):
        self.ci = ci                # The CCDash instance
        self.items = items          # Test items (list of TestItem)
        self.t0 = self.t1 = 0       # Test start and end time
        self.last_err = 0           # Last test error

    def get_name(self):
        return "test"

    def get_info(self):
        return "test"

    def exit_code(self):
        return self.last_err

    def execute(self):
        self.t0 = self.t1 = self.ci.gettime()
        for t in self.items:
            t.execute()
            if t.exit_code()!=0:
                self.last_err = t.exit_code()
        self.t1 = self.ci.gettime()

    def create_xml(self):
        # <Site>
        s = self.ci.create_node()
        # <Testing>
        testing = Node("Testing")
        s.insertNode(testing)
        # <StartDateTime>
        testing.insertNode(Node("StartDateTime",
                                body=self.ci.encodetime(self.t0)))
        # <EndDateTime>
        testing.insertNode(Node("EndDateTime",
                                body=self.ci.encodetime(self.t1)))
        # <ElapsedMinutes>
        testing.insertNode(Node("ElapsedMinutes",
                                body=self.ci.encodedur(self.t1-self.t0)))
        # <TestList>
        testlist = Node("TestList")
        testing.insertNode(testlist)
        for t in self.items:
                if not t.disabled:
                        testlist.insertNode(Node("Test", body=t.fullname))
        # Individual <Test> results
        for t in self.items:
                if not t.disabled:
                        testing.insertNode(t.create_xml())
        return s



class Configure(Operation):
    """\
    This class contains functionalities to perform "./configure" submission.

    """
    @classmethod
    def create_from_xml(cls, ci, node):
        """\
        Factory method to create the instance from an xml node.
        Attributes:
            - cmd: mandatory, the full cmdline
            - wdir: optional, working directory
        """
        cmd = node.getAttribute("cmd")
        if not cmd:
            return None
        wdir = os.path.join(ci.wdir, node.getAttribute("wdir"))
        obj = cls(ci, cmd, wdir)
        if node.getAttribute("disabled") and node.getAttribute("disabled")!="0":
                obj.disabled = True
        return obj

    def __init__(self, ci, cmd, wdir=None):
        self.disabled = False
        self.ci = ci                # CCDash instance
        self.cmd = cmd              # configure command
        self.wdir = wdir            # working directory
        self.t0 = self.t1 = 0       # start and end time
        self.output = None          # Command output
        self.status = -1            # execution status

    def get_name(self):
        return "configure"

    def get_info(self):
        return "configure cmd='%s'" % (self.cmd)

    def success(self):
        return self.status == 0

    def exit_code(self):
        return self.status

    def execute(self):
        if self.disabled:
                self.ci.trace(1, "Configure operation disabled")
                return
        self.t0 = self.t1 = self.ci.gettime()
        self.ci.trace(1, "Running configure test: '" + self.cmd + "'...")
        if self.wdir:
            cwd = os.getcwd()
            os.chdir(self.wdir)
            self.ci.trace(2, "  setting workdir: chdir " + self.wdir)
        else:
            cwd = None
        ret = self.ci.exec_cmd(self.cmd, ret_output=True)
        if cwd:
            os.chdir(cwd)
        if ret.error():
            self.status = 1
            if ret.output:
                self.output = ret.output
            else:
                self.output = str(ret)
        else:
            self.status = 0
            self.output = ret.output
        self.t1 = self.ci.gettime()

    def create_xml(self):
        if self.disabled:
                return None
        # <Site>
        s = self.ci.create_node()
        # <Configure>
        c = Node("Configure")
        s.insertNode(c)
        c.insertNode(Node("ConfigureCommand", body=self.cmd))
        c.insertNode(Node("ConfigureStatus", body=str(self.status)))
        c.insertNode(Node("StartDateTime", body=self.ci.encodetime(self.t0)))
        c.insertNode(Node("EndDateTime", body=self.ci.encodetime(self.t1)))
        c.insertNode(Node("ElapsedMinutes",
                          body=self.ci.encodedur(self.t1-self.t0)))
        c.insertNode(Node("Log", body=self.ci.encode_log(self.status==0,
                                                         self.output)))
        return s



class CommitInfo:
    """\
    This class describes SCM commit information.

    """
    def __init__(self, rev, author, date, msg, items):
        self.rev = rev          # Revision id
        self.author = author    # Author
        self.date = date        # Commit date
        self.msg = msg          # Comit log
        self.items = items      # List of items. Item is sequence of:
                                #  (path, action).
                                # Example:
                                #  ('/path/to/file', 'M')

class FileRevInfo:
    """\
    This class describes file changes info.

    """
    def __init__(self, fullname):
        self.fullname = fullname
        self.file = os.path.basename(fullname)
        self.dir = os.path.dirname(fullname)
        self.commit_info = []

    def add_rev(self, commit_info, op="M"):
        self.commit_info.append(commit_info)


class Update(Operation):
    """\
    This class contains functionalities to check local copy against repository,
    and update if necessary, and report the result to CDash.

    """
    @classmethod
    def create_from_xml(cls, ci, node):
        """\
        Factory method to create the instance from an xml node.
        Attributes:
            - cmd: mandatory, the full cmdline
            - wdir: optional, working directory
            - type: optional, SCM type
            - checkonly: optional, do not actually update
        """
        wdir = os.path.join(ci.wdir, node.getAttribute("wdir"))
        type = node.getAttribute("type")
        if not type:
            type = "SVN"
        checkonly = (node.getAttribute("checkonly") != "")
        obj = cls(ci, wdir, type, checkonly)
        if node.getAttribute("disabled") and node.getAttribute("disabled")!="0":
                obj.disabled = True
        return obj

    def __init__(self, ci, wdir, type="SVN", checkonly=False):
        self.disabled = False
        self.ci = ci                # CCDash instance
        self.wdir = wdir            # Working directory (optional)
        self.no_update = checkonly  # Don't execute svn update
        self.t0 = self.t1 = 0       # Time measurement
        self.base = None            # BASE revision info
        self.head = None            # HEAD revision info
        self.file_revs = {}         # Dictionary of filename vs FileRevInfo
        self.status = ""            # Execution status
        self.type = type            # SCM type
        self.log = ""               # Log output
        if type=="SVN":
            self.cmd = "svn up"     # Update command"
        else:
            self.ci.err_exit("Unsupported repository type")

    def get_name(self):
        return "update"

    def get_info(self):
        return "update cmd='%s' wdir='%s'" % (self.cmd, self.wdir)

    def success(self):
        return True

    def _svn_get_info(self, rev="", url=""):
        revision = ""
        author = ""
        date = ""
        url = ""

        cmd = "svn info --non-interactive --xml"
        if rev:
            cmd = cmd + " -r "+rev
        if url:
            cmd = cmd + " " + url

        ret = self.ci.exec_cmd(cmd, ret_output=True)
        if ret.error():
            self.ci.err_exit(str(ret))

        xinfo = xml.dom.minidom.parseString(ret.output)
        xentry = xinfo.getElementsByTagName("entry")[0]
        #url = xentry.getElementsByTagName("url")[0].childNodes[0].data
        xrepos = xentry.getElementsByTagName("repository")[0]
        url = xrepos.getElementsByTagName("root")[0].childNodes[0].data
        xcommit = xentry.getElementsByTagName("commit")[0]
        revision = xcommit.getAttribute("revision")
        author = xcommit.getElementsByTagName("author")[0].childNodes[0].data
        date = xcommit.getElementsByTagName("date")[0].childNodes[0].data

        return revision, author, date, url

    def _svn_get_commit_info(self, r1, r2="", url=""):
        cmd = "svn log --non-interactive --xml -v -r " + r1
        if r2:
            cmd = cmd + ":" + r2
        if url:
            cmd = cmd + " " + url

        ret = self.ci.exec_cmd(cmd, ret_output=True)
        if ret.error():
            self.ci.err_exit(str(ret))

        xlog = xml.dom.minidom.parseString(ret.output)
        xentries = xlog.getElementsByTagName("logentry")
        if len(xentries)==0:
            self.ci.err_exit("No info from command: " + cmd)

        cis = []
        for xentry in xentries:
            rev = xentry.getAttribute("revision")
            author = xentry.getElementsByTagName("author")[0].childNodes[0].data
            date = xentry.getElementsByTagName("date")[0].childNodes[0].data
            log = xentry.getElementsByTagName("msg")[0].childNodes[0].data
            xpathss = xentry.getElementsByTagName("paths")[0]
            xpaths = xpathss.getElementsByTagName("path")
            items = []
            for xpath in xpaths:
                items.append( (xpath.childNodes[0].data,
                               xpath.getAttribute("action")) )
            ci = CommitInfo(rev, author, date, log, items)
            cis.append(ci)

        return cis

    def exit_code(self):
        # This implementation currently will call sys.exit(1) if it encounters
        # any errors, so if we get to here then all should be okay.
        return 0

    def _svn_execute(self):
        self.ci.trace(1, "Checking SVN work dir")
        # Get BASE info
        rev, author, date, svn_url = self._svn_get_info("BASE")
        self.base = self._svn_get_commit_info(rev, url=svn_url)[0]

        # Get HEAD info
        self.head = self._svn_get_commit_info("HEAD", url=svn_url)[0]

        # log
        logs = self._svn_get_commit_info(self.base.rev, r2=self.head.rev,
                                         url=svn_url)

        # Erase HEAD element
        if len(logs) and logs[0].rev == self.base.rev:
            logs.remove(logs[0])

        # Build the file_revs
        for log in logs:
            for item in log.items:
                if not item[0] in self.file_revs:
                    self.file_revs[item[0]] = FileRevInfo(item[0])
                self.file_revs[item[0]].add_rev(log, item[1])

        # svn update
        if self.base.rev==self.head.rev:
            self.ci.trace(1, "  source directory is up to date")
        else:
            self.ci.trace(1, "  local as at rev %s, repository is at rev %s" %\
                             (self.base.rev, self.head.rev))
            if self.no_update:
                self.ci.trace(1, "  svn update is disabled by cmd-line")
            else:
                ret = self.ci.exec_cmd("svn update --non-interactive")
                if ret.error():
                    self.ci.err_exit(str(ret))

    def execute(self):
        """\
        Perform local update and save the status.

        """
        if self.disabled:
                self.ci.trace(1, "Update operation disabled")
                return
        self.t0 = self.t1 = self.ci.gettime();
        if self.wdir:
            cwd = os.getcwd()
            os.chdir(self.wdir)
        else:
            cwd = None
        if self.type=="SVN":
            self._svn_execute()
        else:
            if cwd:
                os.chdir(cwd)
            self.ci.err_exit("Unsupported repository type: " + self.type)
        if cwd:
            os.chdir(cwd)
        self.t1 = self.ci.gettime();

    def old_check_status(self):
        """\
        Check whether local copy is up to date.

        Returns 0 if up to date, 1 if not.
        """
        old_no_update = self.no_update
        self.no_update = True
        self.execute()
        self.no_update = old_no_update
        self.ci.trace(2, "  BASE: rev=%s, author=%s, date=%s" %
                         (self.base.rev, self.base.author,  self.base.date))
        self.ci.trace(2, "  HEAD: rev=%s, author=%s, date=%s" %
                         (self.head.rev, self.head.author,  self.head.date))
        self.ci.trace(2, self.log)
        if self.base.rev==self.head.rev:
            self.ci.trace(0, "Up to date")
            return 0
        else:
            self.ci.trace(0, "You have old source")
            return 1

    def check_status(self):
        c = pysvn.Client()
	c.callback_ssl_server_trust_prompt = lambda t: (True, t['failures'], True)

        st = c.status(self.wdir, get_all = False, update = True, ignore = True)
        boring_status = [pysvn.wc_status_kind.ignored,
                         pysvn.wc_status_kind.unversioned,
                         pysvn.wc_status_kind.external,
                         pysvn.wc_status_kind.none
                        ]
        need_update = 0

        for e in st:
            if not e.repos_text_status in boring_status:
                self.ci.trace(0, "  svn: " + str(e.repos_text_status) + "\t" + e.path)
                need_update = 1

        if not need_update:
            self.ci.trace(0, "Up to date")

        return need_update

    def create_xml(self):
        if self.disabled:
                return None
        # <Update>
        up = Node("Update", attrs={"Mode": "Client", "Generator": PROG})
        up.insertNode(Node("Site", body=self.ci.site_name))
        up.insertNode(Node("BuildName", body=self.ci.build_name))
        up.insertNode(Node("BuildStamp", body=self.ci.stamp))
        up.insertNode(Node("UpdateCommand", body=self.cmd))
        up.insertNode(Node("UpdateType", body=self.type))
        up.insertNode(Node("UpdateReturnStatus", body=self.status))
        up.insertNode(Node("StartDateTime", body=self.ci.encodetime(self.t0)))
        up.insertNode(Node("EndDateTime", body=self.ci.encodetime(self.t1)))
        up.insertNode(Node("ElapsedMinutes",
                           body=self.ci.encodedur(self.t1-self.t0)))
        # <Directory>
        d = Node("Directory")
        up.insertNode(d)
        if self.wdir:
            wdir=self.wdir
        else:
            wdir = os.getcwd()
        d.insertNode(Node("Name", body=wdir))

        for fname, frevinfo in self.file_revs.iteritems():
            # <Updated>
            ud = Node("Updated")
            d.insertNode(ud)
            ud.insertNode(Node("File", attrs={"Directory": frevinfo.dir},
                               body=frevinfo.file))
            ud.insertNode(Node("Directory", body=frevinfo.dir))
            ud.insertNode(Node("FullName", body=frevinfo.fullname))
            ud.insertNode(Node("CheckinDate",
                               body=frevinfo.commit_info[-1].date))
            ud.insertNode(Node("Author",
                               body=frevinfo.commit_info[-1].author))
            ud.insertNode(Node("Log",
                               body=frevinfo.commit_info[-1].msg))
            ud.insertNode(Node("Revision",
                               body=self.head.rev))
            if self.head.rev != frevinfo.commit_info[0].rev:
                ud.insertNode(Node("PriorRevision",
                                   body=frevinfo.commit_info[0].rev))
                # <Revisions>
                for ci in frevinfo.commit_info:
                    xrevs = Node("Revisions")
                    ud.insertNode(xrevs)
                    xrevs.insertNode(Node("Revision", body=ci.rev))
                    xrevs.insertNode(Node("Author", body=ci.author))
                    xrevs.insertNode(Node("Date", body=ci.date))
                    xrevs.insertNode(Node("Comment", body=ci.msg))

        return up



class FileWrite(Operation):
    """\
    This class represents FileWrite operation in XML scenario file.

    """
    @classmethod
    def create_from_xml(cls, ci, node):
        """\
        Factory method to create the instance from an xml node.
        Attributes:
            - file:    mandatory, the file path
            - replace: optional, occurrence of this text in the file will be
                       replaced by the content
            - replace_begin, replace_end: optional, if both are present, any
                       text between replace_begin and replace_end texts will
                       be replaced by the content
            - saveas:  optional, save as new file
            - content as text body
        """
        file = node.getAttribute("file")
        if not file:
            return None
        file = os.path.join(ci.wdir, file)
        replace = node.getAttribute("replace")
        replace_begin = node.getAttribute("replace_begin")
        replace_end = node.getAttribute("replace_end")
        saveas = node.getAttribute("saveas")
        if saveas:
                saveas = os.path.join(ci.wdir, saveas)
        content = ""
        for c in node.childNodes:
            if c.nodeType == xml.dom.Node.TEXT_NODE:
                #content = c.nodeValue
                content = c.nodeValue
            elif c.nodeType == xml.dom.Node.CDATA_SECTION_NODE:
                content = c.data
                break


        obj = cls(ci, file, content, replace=replace, \
                  replace_begin=replace_begin, replace_end=replace_end, \
                  saveas=saveas)
        if node.getAttribute("disabled") and node.getAttribute("disabled")!="0":
                obj.disabled = True
        return obj

    def __init__(self, ci, file, content, replace="", replace_begin="", replace_end="", saveas=""):
        self.disabled = False
        self.ci = ci
        self.file = file
        self.content = content
        self.replace = replace
        self.replace_begin = replace_begin
        self.replace_end = replace_end
        if not self.replace_begin or not self.replace_end:
                self.replace_begin = self.replace_end = ""
        self.saveas = saveas
        if not self.saveas:
                self.saveas = self.file

    def get_name(self):
        return "filewrite"

    def get_info(self):
        return "filewrite file='%s'" % (self.file)

    def success(self):
        return True

    def execute(self):
        if self.disabled:
                self.ci.trace(1, "FileWrite operation disabled")
                return

        content = ""
        if self.replace:
                f = open(self.file, "r")
                content = f.read()
                f.close()
                if content.find(self.replace) < 0:
                        self.ci.trace(1, "  Warning: pattern '%s' is not found in %s" % (self.replace, self.file))
                content = content.replace(self.replace, self.content)
        elif self.replace_begin:
                pat = re.compile(self.replace_begin + ".*?" + self.replace_end, re.DOTALL)
                f = open(self.file, "r")
                content = f.read()
                f.close()
                if not pat.search(content):
                        self.ci.trace(1, "  Warning: patterns '%s' and '%s' are not found in %s" % (self.replace_begin, self.replace_end, self.file))
                content = pat.sub(self.replace_begin + self.content + self.replace_end, content)
        else:
                content = self.content
        # Write to file
        f = open(self.saveas, "w")
        f.write(content)
        f.close()

    def create_xml(self):
        return None


def create_ccdash(options, check_options=True):
    """\
    Create CCDash instance and configure it with the command line options

    """
    if check_options:
        if  options.url and options.site_name and \
            options.timestamp and options.build_name and options.group:
            pass
        else:
            sys.stderr.write("Error: one or more mandatory options " + \
                             "are missing\n")
            return None
    else:
        options.url = options.site_name = options.build_name = \
            options.timestamp = options.group = ""

    ccdash = CCDash(options.url, options.site_name, options.build_name,
                    options.timestamp + "-" + options.group, options.wdir)
    ccdash.no_upload = options.no_upload
    ccdash.xml_out = options.output
    ccdash.verbosity = options.v
    ccdash.log_level = options.loglevel
    ccdash.last_log = options.lastlog
    ccdash.max_log = options.maxlog
    return ccdash


def cmd_build(options, args):
    """\
    Perform build operation.

    """
    if len(args) != 2:
        sys.stderr.write("Error: the build CMDLINE is not specified\n")
        return 1

    ccdash = create_ccdash(options)
    if ccdash is None:
        return 1
    build = Build(ccdash, args[1], wdir=options.wdir,
                  build_log=options.build_log,
                  build_log_file=options.buildlogfile)
    build.execute()
    xml = build.create_xml()
    if xml:
        ccdash.upload_xml(str(xml))
    return build.exit_code()


def cmd_test(options, args):
    """\
    Perform test operation.

    """
    if len(args) != 3:
        sys.stderr.write("Error: the test NAME and/or CMDLINE is not "+
                         "specified\n")
        return 1

    ccdash = create_ccdash(options)
    if ccdash is None:
        return 1

    ccdash.t1 = options.t1

    item = TestItem(ccdash, args[1], args[1], args[2], options.wdir)
    test = Test(ccdash, [item])
    test.execute()
    xml = test.create_xml()
    if xml:
        ccdash.upload_xml(str(xml))
    return test.exit_code()


def cmd_status(options):
    """\
    Perform status operation.

    """
    ccdash = create_ccdash( options, check_options=False)
    if ccdash is None:
        return 1

    u = Update(ccdash, options.wdir, checkonly=True)
    return u.check_status()


def cmd_update(options):
    """\
    Perform update operation.

    """
    ccdash = create_ccdash(options)
    if ccdash is None:
        return 1

    u = Update(ccdash, options.wdir, "SVN", options.no_update)
    u.execute()
    xml = u.create_xml()
    if xml:
        ccdash.upload_xml(str(xml))
    return u.exit_code()


def cmd_configure(options, args):
    """\
    Perform configure operation.

    """
    if len(args) != 2:
        sys.stderr.write("Error: the configure CMDLINE is not specified\n")
        return 1

    ccdash = create_ccdash(options)
    if ccdash is None:
        return 1

    conf = Configure(ccdash, args[1], wdir=options.wdir)
    conf.execute()
    xml = conf.create_xml()
    if xml:
        ccdash.upload_xml(str(xml))
    return conf.exit_code()


def cmd_merge(options, args):
    """\
    Perform merge operation.

    """
    if len(args) < 2:
        sys.stderr.write("Error: no files are specified\n")
        return 1

    ccdash = create_ccdash(options)
    if ccdash is None:
        return 1

    args.remove(args[0])
    return ccdash.merge_files(args)


def cmd_upload(options, args):
    """\
    Upload file

    """
    if len(args) != 2:
        sys.stderr.write("Error: need exactly one file argument to upload\n")
        return 1

    if not options.url:
        sys.stderr.write("Error: URL not specified\n")
        return 1

    ccdash = CCDash(options.url, "", "", "")

    cmdline = "curl -s -f -T %s %s" % (args[1], options.url)
    ret = ccdash.exec_cmd(cmdline)
    if ret.output:
        ccdash.trace(2, "   " + ret.output)
    if ret.error():
        sys.stderr.write("%s\n" % str(ret))
        return 1
    else:
        sys.stdout.write("Upload success\n")
    return 0


class Submission:
    """\
    One submission entry in scenario file.
    """
    @classmethod
    def create_from_xml(cls, options, submit_node):
        """\
        Factory method to create the instance from an xml node.
        Attributes:
            - group: mandatory, group name
        """

        # Copy options
        opts = copy.copy(options)

        if submit_node.nodeName.lower() != "submit":
            sys.stderr.write("Error: expecting <Submit> node below <Scenario> node, found <%s>\n" % (submit_node.nodeName))
            return None

        if not opts.group:
                opts.group = submit_node.getAttribute("group")
        if not opts.group:
            sys.stderr.write("Error: missing 'group' attribute in <Submit> and not specified in cmdline\n")
            return None

        opts.build_name = submit_node.getAttribute("build")
        if not opts.build_name:
            sys.stderr.write("Error: missing 'build' attribute in <Submit>\n")
            return None


        #opts.no_upload = True

        submit = cls(opts)
        if not submit.ci:
            return None

        submit.set_exclude(submit_node.getAttribute("exclude"))

        for node in submit_node.childNodes:
            ops_list = submit.ops

            if node.nodeType != xml.dom.Node.ELEMENT_NODE:
                continue

            opname = node.nodeName.lower()

            op = None
            if opname == "update":
                op = Update.create_from_xml(submit.ci, node)
            elif opname == "filewrite":
                op = FileWrite.create_from_xml(submit.ci, node)
            elif opname == "configure":
                op = Configure.create_from_xml(submit.ci, node)
            elif opname == "build":
                op = Build.create_from_xml(submit.ci, node)
            elif opname == "test":
                op = TestItem.create_from_xml(submit.ci, node)
                if op:
                    if not submit.test:
                        submit.test = Test(submit.ci, [])
                        submit.ops.append(submit.test)
                    ops_list = submit.test.items
            else:
                sys.stderr.write("Error: invalid node name <%s>\n" % (node.nodeName))
                return None

            if not op:
                sys.stderr.write("Error: error parsing <%s> node\n" % (node.nodeName))
                return None
            elif submit.is_op_excluded(op):
                submit.ci.trace(1, "  skipping %s (excluded)..." % (op.get_info()))
            else:
                submit.ci.trace(1, "  adding %s..." % (op.get_info()))
                ops_list.append(op)
                #submit.ci.trace(1, "  %s added" % (op.get_name()))

        return submit

    def __init__(self, options):
        self.options = options
        self.ci = create_ccdash(options)
        if not self.ci:
            return
        self.tmpdirname = os.path.join(os.getcwd(), "tmp", "submit-" + self.ci.stamp)
        self.test = None
        self.ops = []
        self.re = None
        self.ci.trace(1, "Submission created")

    def set_exclude(self, pattern):
        if (pattern):
                self.re = re.compile(pattern, re.I | re.DOTALL)
        else:
                self.re = None

    def is_op_excluded(self, op):
        if self.re:
                return self.re.match(op.get_info())
        else:
                return False

    def execute(self):
        # Create or clear temp dir
        if os.path.exists(self.tmpdirname):
            shutil.rmtree(self.tmpdirname)
        os.makedirs(self.tmpdirname)

        idx = 1
        out_files = []
        no_upload = self.ci.no_upload
        self.ci.no_upload = True
        fatal_err = False
        for op in self.ops:
            self.ci.xml_out = os.path.join(self.tmpdirname, "%03d-%s.xml" % (idx, op.get_name())  )
            op.execute()
            xml = op.create_xml()
            if xml:
                self.ci.upload_xml(str(xml))
                # Put "Build" operation as first element
                if op.get_name().lower() == "build":
                        out_files.insert(0, self.ci.xml_out)
                else:
                        out_files.append(self.ci.xml_out)
            if op.get_name() == "configure":
                    if not op.success():
                            fatal_err = True
                            self.ci.trace(1, "  configure failed..!")
            elif op.get_name() == "build":
                    if not op.success():
                            fatal_err = True
                            self.ci.trace(1, "  build failed..!")
                    if fatal_err:
                            self.ci.trace(1, "Scenario is stopping due to fatal error")
                            break
            idx = idx + 1

        self.ci.no_upload = no_upload

        # Create script to upload manually
        # UPLOAD.BAT
        fname = os.path.join(self.tmpdirname, "UPLOAD.BAT")
        f = open(fname, "w")
        for fname in out_files:
                f.write("curl -T \"%s\" %s\r\n" % (fname, self.ci.submit_url))
        f.close()


        # Merge all files
        if False:
                # This doesn't work because CDash expects a Build submission first,
                # and other items as separate submissions
                self.ci.trace(1, "Merging and uploading..")
                self.ci.xml_out = self.tmpdirname + ".xml"
                self.ci.merge_files(out_files)
        elif not self.ci.no_upload:
                self.ci.trace(1, "Uploading files..")
                self.ci.xml_out = os.path.join(self.tmpdirname, "tmp.xml")
                for fname in out_files:
                        f = open(fname, "rb")
                        data = f.read()
                        f.close()
                        self.ci.upload_xml(data)
        else:
                self.ci.trace(1, "Not uploading (disabled by cmdline). You can upload manually")

def cmd_scenario(options, args):
    if len(args) != 2:
        sys.stderr.write("Error: need exactly one scenario file argument\n")
        return 1

    doc = xml.dom.minidom.parse(args[1])
    scenario = doc.documentElement
    if scenario.nodeName.lower() != "scenario":
        sys.stderr.write("Error: missing <Scenario> root node\n")
        return 1

    if not options.site_name:
        options.site_name = scenario.getAttribute("site")
    if not options.site_name:
        sys.stderr.write("Error: site name is not specified. You need to specify either in scenario file or in cmdline.\n")
        return 1

    if not options.url:
        options.url = scenario.getAttribute("url")
    if not options.url:
        sys.stderr.write("Error: URL is not specified. You need to specify either in scenario file or in cmdline.\n")
        return 1

    if not options.wdir:
        options.wdir = scenario.getAttribute("wdir")
    if not options.wdir:
        sys.stderr.write("Error: wdir is not specified. You need to specify working directory either in scenario file or in cmdline.\n")
        return 1

    options.timestamp = time.strftime("%Y%m%d-%H%M-%S", time.localtime())

    # Parse submissions specs and create the scenario entries
    subs = []
    for subnode in scenario.childNodes:
        if subnode.nodeType != xml.dom.Node.ELEMENT_NODE:
            continue
        sub = Submission.create_from_xml(options, subnode)
        if not sub:
            return 1
        subs.append(sub)

    # Execute them!
    for submit in subs:
        # Execute all tests
        submit.execute()

    sys.stdout.write("Done\n")

#
# main()
#
# This also can be used as the main entry to ccdash when it is used as
# a Python module. Just construct 'args' parameter like sys.argv.
#
def main(args):
    usage = """%prog operation mandatory-options [options]

operations:
  status                Check whether the source directory is up-to-date with
                        the repository. Returns 0 if up-to-date and non-zero
                        if not. No upload to CDash will be performed.

  update                Perform source control update on the work directory,
                        and upload the result to CDash.

  configure CMDLINE     Perform the specified CMDLINE to configure the source
                        and upload the result to CDash.

  build BUILDCMD        Perform build operation as specified by BUILDCMD,
                        and upload the result to CDash.

  test NAME CMDLINE     Perform a unit test operation as specified by CMDLINE,
                        and upload the result to CDash server as test entry
                        NAME.

  merge FILE1 FILE2 ..  Merge files FILE1, FILE2, etc into one single file and
                        upload the result to CDash

  upload FILE           Upload file FILE to CDash

  scenario FILE         Execute XML scenario in FILE

Sample session:
  $ ccdash.py build "make clean && make dep && make all" \\
      -w /path/to/project \\
      -U http://dash.example.org/submit.php?project=Hello \\
      -S testsite \\
      -T 20081220-2055 \\
      -B linux-gcc-4.1.1 \\
      -G Experimental
"""

    parser = OptionParser(usage=usage, version=PROG)
    group = OptionGroup(parser, "Mandatory Options (except for status " + \
                                "operation)")
    group.add_option("-U", "--url", dest="url",
                     help="Specify the full URL to submit the XML document "+ \
                          " to. Example: " +
                          "http://dash.pjsip.org/submit.php?project=Hello")
    group.add_option("-S", "--site", dest="site_name",
                     help="Specify the site name to identify the site that "+ \
                           "submits this operation")
    group.add_option("-T", "--timestamp", dest="timestamp",
                     help="Specify build timestamp (e.g. 20080331-0210). " +  \
                          "The build timestamp is used to group together the"+\
                          " individual submission into a single build row in"+\
                          " CDash")
    group.add_option("-B", "--build", dest="build_name",
                     help="Specify build identification (e.g. linux-gcc-4.1.1)")
    group.add_option("-G", "--group", dest="group",
                     help="Specify the CDash group name where the result will"+\
                          " be submitted to (e.g. Nightly, Continuous, " + \
                          "Experimental)")
    parser.add_option_group(group)

    group = OptionGroup(parser, "Update options")
    group.add_option("", "--no-update", action="store_true", dest="no_update",
                     help="Do not perform update on local copy")
    parser.add_option_group(group)

    group = OptionGroup(parser, "Build options")
    group.add_option("", "--build-log", action="store_true", default=False,
                     dest="build_log",
                     help="Always include build log in build submissions " + \
                          "(default is no)")
    group.add_option("", "--build-log-file", dest="buildlogfile", default="",
                     help="Store the plain-text version of build output " + \
                          "logs to this file.")
    parser.add_option_group(group)

    group = OptionGroup(parser, "Test options")
    group.add_option("", "--t1", type="int", default=TEST_TIMEOUT, dest="t1",
                     help="Set the test timeout to T1 seconds. Default " +\
                          "is %d seconds (%d minutes)." % \
                          (TEST_TIMEOUT, TEST_TIMEOUT/60))
    parser.add_option_group(group)

    group = OptionGroup(parser, "General options")
    group.add_option("-w", "--work-dir", dest="wdir",
                      help="Specify working directory to execute the " + \
                           "command. If this is not specified, the command" +\
                           " will execute from current directory.")
    group.add_option("-l", "--log-level", dest="loglevel", default=2,
                     type="int",
                     help="Specify the output/logging level to send to CDash"+\
                          ". Valid range is 0-2. " + \
                          "Value 0: never send output log. Value 1: only " + \
                          "send output log on error. Value 2: "  + \
                          "always send output log (default).")
    group.add_option("", "--max-log", type="int", dest="maxlog",
                     default=MAXLOG,
                     help="Limit the output log to MAXLOG bytes. Default is "+\
                          str(MAXLOG) + " bytes.")
    group.add_option("", "--first-log", action="store_false", dest="lastlog",
                     default=True,
                     help="If this is set and --max-log is set, then send " + \
                          "the frst MAXLOG bytes of output rather than " + \
                          "the last MAXLOG bytes to CDash.")
    group.add_option("-y", "--dry-run", action="store_true", dest="no_upload",
                     help="Only run the test, do not submit the result to" + \
                          " CDash. This will perform all the operations, " + \
                          "except that results will not be sumbitted to " + \
                          "CDash.")
    group.add_option("-o", "--output", dest="output",
                     help="Save the XML file to OUTPUT file. This is useful"+\
                          " to inspect the generated XML document or to " +\
                          "submit the XML file manually to CDash.")
    group.add_option("-v", "--verbose", action="count", dest="v", default=1,
                     help="Dump commands output to stdout")
    group.add_option("-q", "--quiet", action="store_false", dest="v",
                     help="Print as little as possible to stdout")
    parser.add_option_group(group)

    (options, args) = parser.parse_args(args)

    args.remove(args[0])

    cwd = os.getcwd()

    if len(args)==0:
        parser.print_help()
        rc = 1
    elif args[0]=="build":
        rc = cmd_build(options, args)
    elif args[0]=="test":
        rc = cmd_test(options, args)
    elif args[0]=="status":
        rc = cmd_status(options)
    elif args[0]=="update":
        rc = cmd_update(options)
    elif args[0]=="configure":
        rc = cmd_configure(options, args)
    elif args[0]=="merge":
        rc = cmd_merge(options, args)
    elif args[0]=="upload":
               rc = cmd_upload(options, args)
    elif args[0]=="scenario":
           rc = cmd_scenario(options, args)
    else:
        print "Error: unknown command '" + args[0] + "'"
        parser.print_help()
        rc = 1

    os.chdir(cwd)
    return rc

if __name__ == "__main__":
    rc = main(sys.argv)
    sys.exit(rc)
