# $Id: 100_simplecall.py 2392 2008-12-22 18:54:58Z bennylp $
#
from inc_cfg import *

# Simple call
test_param = TestParam(
		"Basic call",
		[
			InstanceParam("Alice", "--id sip:alice@10.132.255.158 --null-audio", sip_port=5060)
			#InstanceParam("Alice", "--id sip:alice@10.0.1.25 --null-audio --local-port=5060", sip_port=5060)
		]
)
