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
	alice = t.process[0]

	# if have_reg then wait for couple of seconds for PUBLISH
	# to complete (just in case pUBLISH is used)
	
	if alice.inst_param.have_reg:
		time.sleep(1)

	# Alice making call 
	###### step 1 ######
	alice.send("m")
	alice.send("<sip:bob@192.168.1.17>")
	#alice.send("<sip:bob@10.0.1.28>")
	alice.expect(const.STATE_CALLING)
	
	# Callee waits for call and answers with 180/Ringing
	
 	time.sleep(0.2)
	######  steps 5 & 6 #######
	alice.expect("SIP/2.0 180")

	# Synchronize stdout
	alice.sync_stdout()

	# Wait until call is connected in both endpoints

	###### steps 11 & 12 ######
	alice.expect(const.STATE_CONFIRMED)

	# Synchronize stdout
	time.sleep(0.1)
	alice.sync_stdout()

	# Wait a time while carl call to Bob
	time.sleep(10)

	alice.expect(const.MEDIA_HOLD)
	time.sleep(5)
	alice.expect(const.MEDIA_ACTIVE)

	###### steps 53 & 54 ######
	# BYE to Bob 
	alice.send("h")
	alice.expect(const.STATE_DISCONNECTED)

	

# Here where it all comes together
test = cfg_file.test_param
test.test_func = test_func

