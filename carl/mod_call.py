# $Id: mod_call.py 2078 2008-06-27 21:12:12Z nanang $
import time
import imp
import sys
import inc_const as const
from inc_cfg import *

# Load configuration
cfg_file = imp.load_source("cfg_file", ARGS[1])

# Check media flow between ua1 and ua2
def check_media(ua1, ua2):
	ua1.send("#")
	ua1.expect("#")
	ua1.send("1122")
	ua2.expect(const.RX_DTMF + "1")
	ua2.expect(const.RX_DTMF + "1")
	ua2.expect(const.RX_DTMF + "2")
	ua2.expect(const.RX_DTMF + "2")


# Test body function
def test_func(t):
	carl = t.process[0]

	# if have_reg then wait for couple of seconds for PUBLISH
	# to complete (just in case pUBLISH is used)

	if carl.inst_param.have_reg:
		time.sleep(1)

	##### setps 24 & 25 #####
	# Carl calling to Bob
	carl.send("m")
	#carl.send("<sip:bob@10.0.1.28>")
	carl.send("<sip:bob@192.168.1.17>")
	carl.expect(const.STATE_CALLING)
	

	####### step 28 & 29 ######
	carl.expect("SIP/2.0 180")

	# Synchronize stdout
	carl.sync_stdout()

	####### step 36 ######
	# Wait until call is connected in both endpoints
	time.sleep(0.3)
	carl.expect(const.STATE_CONFIRMED)

	###### step 37 & 38 ######
	carl.send("h")
	carl.expect(const.STATE_DISCONNECTED)

# Here where it all comes together
test = cfg_file.test_param
test.test_func = test_func

