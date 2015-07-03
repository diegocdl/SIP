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
	bob = t.process[0]

	# if have_reg then wait for couple of seconds for PUBLISH
	# to complete (just in case pUBLISH is used)
	if bob.inst_param.have_reg:
		time.sleep(1)
	
	# Bob waits for call and answers with 180/Ringing
	time.sleep(0.2)
	
	##### steps 2 & 3 ######
	bob.expect(const.EVENT_INCOMING_CALL)

	##### steps 4 & 5 ######
	bob.send("a")
	bob.send("180")
	bob.expect("SIP/2.0 180")

	# Synchronize stdout
	bob.sync_stdout()

	# Bob answers with 200/OK
	##### steps 10 & 11 ######
	time.sleep(0.2)
	bob.send("a")
	bob.send("200")

	# Wait until call is connected in both endpoints
	time.sleep(0.2)
	bob.expect(const.STATE_CONFIRMED)

	# Synchronize stdout
	bob.sync_stdout()
	time.sleep(0.1)
	
	##### Hold alice's call (step 23) ######
	bob.send("H")
	print "waiting for carl"

	time.sleep(5)
	bob.send("\n")

	##### step 26 ######
	bob.expect(const.EVENT_INCOMING_CALL)

	##### step 27 ######
	bob.send("[")
	bob.send("a")
	bob.send("180")
	bob.expect("SIP/2.0 180")

	# Synchronize stdout
	bob.sync_stdout()

	##### step 32 ######
	##### Bob answers with 200/OK ####
	time.sleep(0.2)
	bob.send("a")
	bob.send("200")
	bob.expect(const.STATE_CONFIRMED)

	##### step 39 ######
	bob.expect(const.STATE_DISCONNECTED)


	##### step 43 & 44 #####
	bob.send("v")
	bob.expect(const.MEDIA_ACTIVE)

	##### step 54 & 55 ######
	bob.expect(const.STATE_DISCONNECTED)
	

	


	

# Here where it all comes together
test = cfg_file.test_param
test.test_func = test_func

