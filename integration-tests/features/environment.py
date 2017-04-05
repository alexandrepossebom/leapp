import contextlib
import json
import pathlib
import subprocess
import time

from attr import attributes, attrib
from hamcrest import assert_that, equal_to

import requests

##############################
# General utilities
##############################
_TEST_DIR = pathlib.Path(__file__).parent.parent
_REPO_DIR = _TEST_DIR.parent

# Command execution helper
def _run_command(cmd, work_dir, ignore_errors):
    print("  Running {} in {}".format(cmd, work_dir))
    output = None
    try:
        output = subprocess.check_output(
            cmd, cwd=work_dir, stderr=subprocess.PIPE
        ).decode()
    except subprocess.CalledProcessError as exc:
        output = exc.output.decode()
        if not ignore_errors:
            print("=== stdout for failed command ===")
            print(output)
            print("=== stderr for failed command ===")
            print(exc.stderr.decode())
            raise
    return output


##############################
# Local VM management
##############################

_VM_HOSTNAME_PREFIX = "leapp-tests-"
_VM_DEFS = {
    _VM_HOSTNAME_PREFIX + path.name: str(path)
        for path in (_TEST_DIR / "vmdefs").iterdir()
}

class VirtualMachineHelper(object):
    """Test step helper to launch and manage VMs

    Currently based specifically on local Vagrant VMs
    """

    def __init__(self):
        self._machines = {}
        self._resource_manager = contextlib.ExitStack()

    def ensure_local_vm(self, name, definition, destroy=False):
        """Ensure a local VM exists based on the given definition

        *name*: name used to refer to the VM in scenario steps
        *definition*: directory name in integration-tests/vmdefs
        *destroy*: whether or not to destroy any existing VM
        """
        hostname = _VM_HOSTNAME_PREFIX + definition
        if hostname not in _VM_DEFS:
            raise ValueError("Unknown VM image: {}".format(definition))
        if destroy:
            # TODO: Look at using "--provision" for fresh VMs
            #       rather than a full destroy/recreate cycle
            #       Alternatively: add "reprovision" as a
            #       separate option for machine creation or
            #       even make it the default for `destroy=False`
            self._vm_destroy(hostname)
        self._vm_up(name, hostname)
        if destroy:
            self._resource_manager.callback(self._vm_destroy, name)
        else:
            self._resource_manager.callback(self._vm_halt, name)

    def get_hostname(self, name):
        """Return the expected hostname for the named machine"""
        return self._machines[name]

    def close(self):
        """Halt or destroy all created VMs"""
        self._resource_manager.close()

    @staticmethod
    def _run_vagrant(hostname, *args, ignore_errors=False):
        # TODO: explore https://pypi.python.org/pypi/python-vagrant
        vm_dir = _VM_DEFS[hostname]
        cmd = ["vagrant"]
        cmd.extend(args)
        return _run_command(cmd, vm_dir, ignore_errors)

    def _vm_up(self, name, hostname):
        result = self._run_vagrant(hostname, "up")
        print("Started {} VM instance".format(hostname))
        self._machines[name] = hostname
        return result

    def _vm_halt(self, name):
        hostname = self._machines.pop(name)
        result = self._run_vagrant(hostname, "halt", ignore_errors=True)
        print("Suspended {} VM instance".format(hostname))
        return result

    def _vm_destroy(self, name):
        hostname = self._machines.pop(name)
        result = self._run_vagrant(hostname, "destroy", ignore_errors=True)
        print("Destroyed {} VM instance".format(hostname))
        return result


##############################
# Leapp commands
##############################

_LEAPP_TOOL = str(_REPO_DIR / "leapp-tool.py")

@attributes
class MigrationInfo(object):
    """Details of local hosts involved in an app migration command

    *local_vm_count*: Total number of local VMs found during migration
    *source_ip*: host accessible IP address found for source VM
    *target_ip*: host accessible IP address found for target VM
    """
    local_vm_count = attrib()
    source_ip = attrib()
    target_ip = attrib()

    @classmethod
    def from_vm_list(cls, machines, source_host, target_host):
        """Build a result given a local VM listing and migration hostnames"""
        vm_count = len(machines)
        source_ip = target_ip = None
        for machine in machines:
            if machine["hostname"] == source_host:
                source_ip = machine["ip"][0]
            if machine["hostname"] == target_host:
                target_ip = machine["ip"][0]
            if source_ip is not None and target_ip is not None:
                break
        return cls(vm_count, source_ip, target_ip)


class MigrationHelper(object):
    """Test step helper to invoke the LeApp CLI

    Requires a VirtualMachineHelper instance
    """

    def __init__(self, vm_helper):
        self._vm_helper = vm_helper

    def redeploy_as_macrocontainer(self, source_vm, target_vm):
        """Recreate source VM as a macrocontainer on given target VM"""
        vm_helper = self._vm_helper
        source_host = vm_helper.get_hostname(source_vm)
        target_host = vm_helper.get_hostname(target_vm)
        self._convert_vm_to_macrocontainer(source_host, target_host)
        return self._get_migration_host_info(source_host, target_host)

    @staticmethod
    def _run_leapp(*args):
        cmd = ["sudo", "/usr/bin/python2", _LEAPP_TOOL]
        cmd.extend(args)
        # TODO: Ensure leapp-tool.py works independently of the working directory
        return _run_command(cmd, work_dir=str(_REPO_DIR), ignore_errors=False)

    @classmethod
    def _convert_vm_to_macrocontainer(cls, source_host, target_host):
        result = cls._run_leapp("migrate-machine", "-t", target_host, source_host)
        msg = "Redeployed {} as macrocontainer on {}"
        print(msg.format(source_host, target_host))
        return result

    @classmethod
    def _get_migration_host_info(cls, source_host, target_host):
        leapp_output = cls._run_leapp("list-machines", "--shallow")
        machines = json.loads(leapp_output)["machines"]
        return MigrationInfo.from_vm_list(machines, source_host, target_host)


##############################
# Service status checking
##############################

class RequestsHelper(object):
    """Test step helper to check HTTP responses"""

    @classmethod
    def get_response(cls, service_url, wait_for_connection=None):
        """Get HTTP response from given service URL

        Responses are returned as requests.Response objects

        *service_url*: the service URL to query
        *wait_for_connection*: number of seconds to wait for a HTTP connection
                               to the service. `None` indicates that a response
                               is expected immediately.
        """
        deadline = time.monotonic()
        if wait_for_connection is None:
            fail_msg = "No response from service"
        else:
            fail_msg = "No response from service within {} seconds".format(wait_for_connection)
            deadline += wait_for_connection
        while True:
            try:
                return requests.get(service_url)
            except Exception:
                pass
            if time.monotonic() >= deadline:
                break
        raise AssertionError(fail_msg)

    @classmethod
    def get_responses(cls, urls_to_check):
        """Check responses from multiple given URLs

        Each URL can be either a string (which will be expected to return
        a response immediately), or else a (service_url, wait_for_connection)
        pair, which is interpreted as described for `get_response()`.

        Response are returned as a dictionary mapping from the service URLs
        to requests.Response objects.
        """
        # TODO: Use concurrent.futures to check the given URLs in parallel
        responses = {}
        for url_to_check in urls_to_check:
            if isinstance(url_to_check, tuple):
                url_to_check, wait_for_connection = url_to_check
            else:
                wait_for_connection = None
            responses[url_to_check] = cls.get_response(url_to_check,
                                                       wait_for_connection)
        return responses

    @classmethod
    def compare_redeployed_response(cls, original_ip, redeployed_ip, *,
                                    tcp_port, status, wait_for_target):
        """Compare a pre-migration app response with a redeployed response

        Expects an immediate response from the original IP, and allows for
        a delay before the redeployment target starts returning responses
        """
        # Get response from source VM
        original_url = "http://{}:{}".format(original_ip, tcp_port)
        original_response = cls.get_response(original_url)
        print("Response received from {}".format(original_url))
        original_status = original_response.status_code
        assert_that(original_status, equal_to(status), "Original status")
        # Get response from target VM
        redeployed_url = "http://{}:{}".format(redeployed_ip, tcp_port)
        redeployed_response = cls.get_response(redeployed_url, wait_for_target)
        print("Response received from {}".format(redeployed_url))
        # Compare the responses
        assert_that(redeployed_response.status_code, equal_to(original_status), "Redeployed status")
        original_data = original_response.text
        redeployed_data = redeployed_response.text
        assert_that(redeployed_data, equal_to(original_data), "Same response")


##############################
# Test execution hooks
##############################

def before_all(context):
    # Some steps require sudo, so for convenience in interactive use,
    # we ensure we prompt for elevated permissions immediately,
    # rather than potentially halting midway through a test
    subprocess.check_output(["sudo", "echo", "Elevated permissions needed"])

    # Use contextlib.ExitStack to manage global resources
    context._global_cleanup = contextlib.ExitStack()

def before_scenario(context, scenario):
    # Each scenario has a contextlib.ExitStack instance for resource cleanup
    context.scenario_cleanup = contextlib.ExitStack()

    # Each scenario gets a VirtualMachineHelper instance
    # VMs are slow to start/stop, so by default, we defer halting them
    # Feature steps can still opt in to eagerly cleaning up a scenario's VMs
    # by doing `context.scenario_cleanup.callback(context.vm_helper.close)`
    context.vm_helper = vm_helper = VirtualMachineHelper()
    context._global_cleanup.callback(vm_helper.close)

    # Each scenario gets a MigrationHelper instance
    context.migration_helper = MigrationHelper(context.vm_helper)

    # Each scenario gets a RequestsHelper instance
    context.http_helper = RequestsHelper()

def after_scenario(context, scenario):
    context.scenario_cleanup.close()

def after_all(context):
    context._global_cleanup.close()