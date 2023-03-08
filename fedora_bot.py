#!/usr/bin/python3

"""Just a small bot to take care of Koji builds and Bodhi updates"""

import argparse
import subprocess
import sys
import os
import re
import pexpect
import requests
from requests.adapters import HTTPAdapter, Retry
from slack_sdk.webhook import WebhookClient


class fg:  # pylint: disable=too-few-public-methods
    """Set of constants to print colored output in the terminal"""
    BOLD = '\033[1m'  # bold
    OK = '\033[32m'  # green
    INFO = '\033[33m'  # yellow
    ERROR = '\033[31m'  # red
    RESET = '\033[0m'  # reset


def msg_error(body):
    """Print error and exit"""
    print(f"{fg.ERROR}{fg.BOLD}Error:{fg.RESET} {body}")
    sys.exit(1)


def msg_info(body):
    """Print info message"""
    print(f"{fg.INFO}{fg.BOLD}Info:{fg.RESET} {body}")


def msg_ok(body):
    """Print ok status message"""
    print(f"{fg.OK}{fg.BOLD}OK:{fg.RESET} {body}")


def run_command(argv):
    """Run a shellcommand and return stdout"""
    result = subprocess.run(  # pylint: disable=subprocess-run-check
        argv,
        capture_output=True,
        text=True,
        encoding='utf-8')

    if result.returncode == 0:
        ret = result.stdout.strip()
    else:
        ret = result.stderr.strip()

    return ret


def kinit(args):
    """Get a Kerberos ticket for FEDORAPROJECT.ORG"""
    domain = "FEDORAPROJECT.ORG"
    print(f"      Get a Kerberos ticket for {args.user}@{domain}")

    child = pexpect.spawn(f'kinit {args.user}@{domain}', timeout=60,
                          echo=False)
    try:
        child.expect(".*:")
        child.sendline(args.password)
    except OSError as err:
        msg_error(f"kinit with pexpect raised OSError: {err}")

    child.wait()
    res = run_command(['klist'])
    if "not found" in res:
        msg_error(f"An error occurred getting a valid Kerberos ticket:\n{res}")


def slack_notify(message: str):
    msg_ok(message)

    url = os.getenv('SLACK_WEBHOOK_URL')
    if not url:
        return

    github_server_url = os.getenv('GITHUB_SERVER_URL')
    github_repository = os.getenv('GITHUB_REPOSITORY')
    github_run_id = os.getenv('GITHUB_RUN_ID')
    github_url = f"{github_server_url}/{github_repository}/actions/runs/{github_run_id}"

    webhook = WebhookClient(url)

    response = webhook.send(
        text="fallback",
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"<{github_url}|fedora-bot>: :fedora-new: {message}"
                }
            }
        ])
    assert response.status_code == 200
    assert response.body == "ok"


def fedora_distgit_http_client() -> requests.Session:
    """
    Returns a configured http client for Fedora's dist-git. We need a custom
    client in order to implement retries. These are needed because
    src.fedoraproject.org proved to be slightly unreliable and without
    retries, some fedora-bot runs failed.
    """
    retry_adapter = Retry(
        total=3,
        backoff_factor=0.1,
        # POST isn't generally idempotent but we only use it to merge PRs
        # which actually is idempotent.
        allowed_methods=["POST"] + list(Retry.DEFAULT_ALLOWED_METHODS),
        # Enable retrying on retryable http codes.
        status_forcelist=[500, 502, 503, 504],
    )
    session = requests.Session()
    session.mount("https://src.fedoraproject.org/", HTTPAdapter(max_retries=retry_adapter))
    return session


def check_pull_request_flags(http, component, pr_id, num_tests):
    """
    Check the test results in the pull request, which are represented in Pagure as 'flags'
    As the test results (pagure flags) are not immediately available and there is no indication of running tests
    we have to hardcode the amount of test results to expect so we can verify all tests have passed.
    """
    test_results = []
    success = False

    req = http.get(f'https://src.fedoraproject.org/api/0/rpms/{component}/pull-request/{pr_id}/flag')
    res = req.json()
    for flag in res['flags']:
        test_results.append(flag['status'])

    if len(test_results) != num_tests: # check if the expected number of tests passed
        msg_info(f"Only {len(test_results)}/{num_tests} tests have run, let's try again later.")
    elif all(r == 'success' for r in test_results):
        msg_ok(f"All {len(test_results)} tests passed so the pull-request can be merged.")
        success = True
    elif 'failure' in test_results:
        msg_info(f"Pull request '{pr_id}' has {len(test_results)}/{num_tests} failed tests and therefore cannot be auto-merged.")
    elif 'pending' in test_results:
        msg_info("Some tests are still running, let's try again later")
    else:
        msg_error("Something is wrong - maybe the amount of tests have changed?")

    return success


def merge_pull_request(http, args, component, pr_id):
    """Merge a single pull request"""
    url = f"https://src.fedoraproject.org/rpms/{component}/pull-request/{pr_id}"

    req = http.post(f'https://src.fedoraproject.org/api/0/rpms/{component}/pull-request/{pr_id}/merge', headers={'Authorization': f'token {args.apikey}'})
    res = req.json()
    if res['message'] == "Changes merged!":
        msg_ok(f"Merged pull request for {component}: {url}")
    else:
        msg_info(res)


def merge_open_pull_requests(args, component, num_tests):
    """
    Try to merge any open pull request that meets the criteria:
     1. it was created by packit
     2. all tests have passed
    """
    http = fedora_distgit_http_client()
    req = http.get(f'https://src.fedoraproject.org/api/0/rpms/{component}/pull-requests?author=packit')
    res = req.json()
    if res['total_requests'] == 0:
        msg_ok(f"There are currently no open pull requests for {component}.")
        return

    msg_info(f"Found {res['total_requests']} open pull requests for {component}. Starting the merge train...")

    for pr in res['requests']:
        successful_checks = check_pull_request_flags(http, component, pr['id'], num_tests)
        if successful_checks:
            merge_pull_request(http, args, component, pr['id'])


def update_bodhi(args, component, fedora):
    """Publish a single Bodhi update"""
    msg_info(f"Updating Bodhi for Fedora {fedora}...")
    kinit(args)
    child = pexpect.spawn("fedpkg update --type enhancement "
                            f"--notes 'Update {component} to the latest version'",
                            timeout=60, echo=False)
    try:
        child.expect(".*:")
        child.sendline(args.password)
    except OSError as err:
        msg_info(f"'fedpkg update' with pexpect raised OSError: {err}")

    child.wait()
    child.read()
    res = str(child.before, 'UTF-8')
    print(res)
    if "update has been submitted" in res:
        url = ""
        for line in res.split("\n"):
            if "https://bodhi.fedoraproject.org" in line:
                url = line.strip()
        slack_notify(f"<{url}|Bodhi update published> for *{component}* in *Fedora {fedora}*. :meow_checkmark:\nThis means the *release for Fedora {fedora} is complete*. :tada:")


def publish_updates(args, component, fedoras):
    """Update Bodhi for all active Fedora releases"""
    work_dir = os.getcwd()
    os.chdir(os.path.join(work_dir, component))

    for fedora in fedoras:
        run_command(['git', 'checkout', f"f{fedora}"])
        print(f"      Checked out branch 'f{fedora}'")
        update_bodhi(args, component, fedora)


def get_latest_dist_git_release(component):
    """Get the latest release version found in dist-git"""
    print(f"      Cloning into 'https://src.fedoraproject.org/rpms/{component}.git'...")
    work_dir = os.getcwd()
    res = run_command(['git','clone',f"https://src.fedoraproject.org/rpms/{component}.git"])
    if res:
        print(res)
    os.chdir(os.path.join(work_dir, component))
    branch = run_command(['git', 'branch', '--show-current'])
    print(f"      Checked out dist-git with branch '{branch}'")

    path = f'{component}.spec'
    with open(path, 'r', encoding='utf-8') as file:
        lines = file.readlines()
        for line in lines:
            if line.startswith("Version:"):
                version = re.search('[0-9.]+', line)

    os.chdir(work_dir)

    if version.group(0) is None:
        msg_error("Could not extract verson from specfile.")

    return version.group(0)


def get_missing_updates(component, fedoras):
    """Check for existing Koji builds that have no Bodhi update published for any active Fedora release"""
    version = get_latest_dist_git_release(component)
    print(f"      Version {component} {version} found in dist-git")

    updates = set()

    # TODO: Drop this ugly workaround once koji-osbuild 7 gets released
    release = "1"
    if component == "koji-osbuild":
        release = "0"

    for fedora in fedoras:
        res = run_command(['koji', 'buildinfo', f'{component}-{version}-{release}.fc{fedora}'])
        if "State: COMPLETE" in res:
            print(f"      Fedora {fedora}: ✅ Build for {component} {version} is available in Koji")
            res = run_command(['bodhi', 'updates', 'query', '--builds', f'{component}-{version}-{release}.fc{fedora}'])
            if "0 updates found" in res:
                print(f"      Fedora {fedora}: No Bodhi update for {component} {version}")
                updates.add(fedora)
            else:
                print(f"      Fedora {fedora}: ✅ Update for {component} {version} is available in Bodhi")
        else:
            msg_info(f"WARNING: There is no build for {component} {version} in Fedora {fedora}. Probably packit is still doing its thing...")

    return list(updates)


def get_fedora_releases():
    """Get all active Fedora releases (exluding rawhide)"""
    # https://github.com/sgallagher/get-fedora-releases-action/blob/main/get_fedora_releases.py
    res = None
    while not res:
        try:
            res = requests.get('https://bodhi.fedoraproject.org/releases?state=current')
        except requests.exceptions.Timeout:
            pass
        except requests.exceptions.HTTPError as err:
            msg_error(err)

    stable = set()
    for release in res.json()['releases']:
        if release['id_prefix'] == "FEDORA":
            stable.add(release['version'])

    return list(stable)


def main():
    """Main function"""
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--component", action='append', metavar="PACKAGE:NUM_TESTS", help="Component name and expected number of tests in PR")
    parser.add_argument("-u", "--user", help="Set the username of the Fedora account")
    parser.add_argument("-p", "--password", help="Set the Fedora account password")
    parser.add_argument("--apikey", help="Set the Fedora account API key")
    args = parser.parse_args()

    if not args.component:
        parser.error("Need to specify at least one --component")

    fedoras = get_fedora_releases()

    for component_numtests in args.component:
        try:
            component, num_tests = component_numtests.split(':')
            num_tests = int(num_tests)
        except ValueError:
            parser.error(f"Invalid component format, must be PACKAGE:NUM_TESTS : {component_numtests}")

        print(f"\n--- {component} ---\n")
        if args.apikey:
            msg_info(f"Checking for open pull requests of {component}...")
            merge_open_pull_requests(args, component, num_tests)
        else:
            msg_info("No Fedora account API key supplied - skipping merging of pull requests.")

        if args.user and args.password: # Only check Bodhi if credentials were supplied
            msg_info(f"Checking for missing updates of '{component}'...")
            missing_updates = get_missing_updates(component, fedoras)

            if missing_updates:
                msg_info(f"Found missing updates in Bodhi: {missing_updates}")
                publish_updates(args, component, missing_updates)
                msg_ok(f"Tried to update {missing_updates}.")
            else:
                msg_ok("No releases found with missing updates.")
        else:
            msg_info("No Fedora credentials supplied - skipping Bodhi updates.")


if __name__ == "__main__":
    main()
