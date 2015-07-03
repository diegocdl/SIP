# $Id: 100_simplecall.py 2392 2008-12-22 18:54:58Z bennylp $
#
from inc_cfg import *

# Simple call
test_param = TestParam(
		"Basic call",
		[
			InstanceParam("Bob", "--id sip:bob@192.168.1.17 --null-audio", sip_port=5060)
#			InstanceParam("Bob", "--id sip:bob@10.0.1.28 --null-audio", sip_port=5060)
		]
		)
