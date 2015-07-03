# $Id: 100_simplecall.py 2392 2008-12-22 18:54:58Z bennylp $
#
from inc_cfg import *

# Simple call
test_param = TestParam(
		"Basic call",
		[
#			InstanceParam("Carl", "--id sip:carl@10.0.1.27 --null-audio", sip_port=5060)		
			InstanceParam("Carl", "--id sip:carl@192.168.1.19 --null-audio", sip_port=5060)		
		]
		)
