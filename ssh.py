import spur
import spur.ssh
import time
print "Conectando con ssh..\n"
bob = spur.SshShell(
    hostname="104.236.93.101", username="root", password="galileo2015",
    missing_host_key=spur.ssh.MissingHostKey.accept)
alice = spur.SshShell(
    hostname="104.236.226.178", username="root", password="galileo2015",
    missing_host_key=spur.ssh.MissingHostKey.accept)

carl = spur.SshShell(
    hostname="45.55.147.205", username="root", password="galileo2015",
    missing_host_key=spur.ssh.MissingHostKey.accept)

with bob:
    print "conectado bob"
    with alice:
        print "conectado alice"
        with carl:
            print "conectado carl"
            for i in range(0, 10):
                print "====================================="
                print "=============" + str(i) + "==============="
                print "====================================="
                result = alice.spawn(['sudo', 'python', './run_scenario.py', 'gnu.xml'])
                result1 = bob.spawn(['sudo', 'python', './run_scenario.py', 'gnu.xml'])
                result2 = carl.spawn(['sudo', 'python', './run_scenario.py', 'gnu.xml'])
                result = result.wait_for_result()
                result1 = result1.wait_for_result()
                result2 = result2.wait_for_result()
                print result.output # prints hello
                print "-------------------------------------------"
                print result1.output # prints hello
                print "-------------------------------------------"
                print result2.output # prints hello
                print "-------------------------------------------"
                time.sleep(5)
