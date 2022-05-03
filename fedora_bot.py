#!/usr/bin/python3

"""Just a small bot to take care of Koji builds and Bodhi updates"""

import argparse
import subprocess
import sys
import os
import re
import pexpect
import requests
import json
from urllib import request
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
    msg_info(f"Get a Kerberos ticket for {args.user}@FEDORAPROJECT.ORG")
    domain = "FEDORAPROJECT.ORG"

    child = pexpect.spawn(f'kinit {args.user}@{domain}', timeout=60,
                          echo=False)
    try:
        child.expect(".*:")
        child.sendline(args.password)
    except OSError as err:
        msg_error(f"kinit with pexpect raised OSError: {err}")

    child.wait()
    res = run_command(['klist'])
    msg_info(f"Currently valid Kerberos tickets:\n{res}")


def slack_notify(message: str):
    url = os.getenv('SLACK_WEBHOOK_URL')
    github_server_url = os.getenv('GITHUB_SERVER_URL')
    github_repository = os.getenv('GITHUB_REPOSITORY')
    github_run_id = os.getenv('GITHUB_RUN_ID')
    github_url = f"{github_server_url}/{github_repository}/actions/runs/{github_run_id}"

    msg_ok(message)

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


def merge_open_pull_requests(args, component):
    req = request.Request(f'https://src.fedoraproject.org/api/0/rpms/{component}/pull-requests?author=packit', method="GET") # returns open PRs created by packit
    req.add_header('Content-Type', 'application/json')
    req.add_header('Authorization', f'token {args.apikey}')

    try:
        result = request.urlopen(req)
        content = result.read()
        if content:
            res = json.loads(content.decode('utf-8'))
            if res['total_requests'] == 0:
                msg_ok(f"There are currently no open pull requests for {component}.")
                return

            msg_info(f"Found {res['total_requests']} open pull requests for {component}. Starting the merge train...")

            for r in res['requests']:
                merge_pull_request(args, component, r['id'])

    except Exception as e:
        msg_info(f"{str(e)}\nFailed to get pull requests for '{component}'.")


def merge_pull_request(args, component, pr_id):
    req = request.Request(f'https://src.fedoraproject.org/api/0/rpms/{component}/pull-request/{pr_id}/merge', method="POST")
    req.add_header('Authorization', f'token {args.apikey}')

    url = f"https://src.fedoraproject.org/rpms/{component}/pull-request/{pr_id}"

    try:
        r = request.urlopen(req)
        content = r.read()
        if content:
            res = json.loads(content.decode('utf-8'))
            if res['message'] == "Changes merged!":
                msg_ok(f"Merged pull request for {component}: {url}")
            else:
                msg_info(res)
    except Exception as e:
        msg_info(f"{str(e)}\nFailed to merge pull request for {component}: {url}")


def update_bodhi(args, component, fedora):
    msg_info(f"Updating bodhi for Fedora {fedora}...")
    child = pexpect.spawn("fedpkg update --type enhancement "
                            f"--notes 'Update {component} to the latest version'",
                            timeout=60, echo=False)
    try:
        child.expect(".*:")
        child.sendline(args.password)
    except OSError as err:
        msg_info(f"kinit with pexpect raised OSError: {err}")

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


def schedule_fedora_builds(args, component, fedoras, missing_updates):
    """Schedule builds for all active Fedora releases with missing builds"""

    kojis = { "osbuild": "https://koji.fedoraproject.org/koji/packageinfo?packageID=29756",
              "osbuild-composer": "https://koji.fedoraproject.org/koji/packageinfo?packageID=31032",
              "koji-osbuild": "https://koji.fedoraproject.org/koji/packageinfo?packageID=32748" }
    msg_info(f"Check {kojis[component]} for all {component} builds.")

    work_dir = os.getcwd()

    os.chdir(os.path.join(work_dir, component))

    for fedora in fedoras:
        msg_info(f"Scheduling build for Fedora {fedora} (this may take a while)")
        if fedora == '37':
            branch = "rawhide"
        else:
            branch = f"f{fedora}"
        res = run_command(['git', 'checkout', branch])
        print(f"      Checked out branch '{branch}'")

        res = run_command(['fedpkg', 'build'])
        print(res)

        if "completed successfully" in res:
            for line in res.split("\n"):
                if "https://koji.fedoraproject.org" in line:
                    url = line.strip("Task info: ")
            if not url:
                url = kojis[component]
            slack_notify(f"<{url}|Koji build> for *{component}* in *Fedora {fedora}* completed successfully. :meow_checkmark:")

            if branch != "rawhide":
                update_bodhi(args, component, fedora)
        elif fedora in missing_updates:
            if branch != "rawhide":
                update_bodhi(args, component, fedora)
        else:
            msg_info(f"Did not build Fedora {fedora}.")
            continue


def get_latest_dist_git_release(component):
    """Get the latest release version found in dist-git"""
    msg_info(f"Cloning into 'https://src.fedoraproject.org/rpms/{component}.git'...")
    work_dir = os.getcwd()
    res = run_command(['git','clone',f"https://src.fedoraproject.org/rpms/{component}.git"])
    print(res)
    os.chdir(os.path.join(work_dir, component))
    branch = run_command(['git', 'branch', '--show-current'])
    print(f"      Checked out dist-git with branch '{branch}'")

    path = f'{component}.spec'
    with open(path, 'r', encoding='utf-8') as file:
        lines = file.readlines()
        for line in lines:
            if line.startswith("Version:"):
                version = re.search('[0-9]+', line)

    os.chdir(work_dir)

    if version.group(0) is None:
        msg_error("Could not extract verson from specfile.")

    return version.group(0)


def check_release_builds(component, fedoras):
    """Check if there are missing builds in Koji for any active Fedora release"""
    version = get_latest_dist_git_release(component)
    print(f"      Version {component} {version} found in dist-git")

    releases = set()
    updates = set()

    for fedora in fedoras:
        res = run_command(['koji', 'buildinfo', f'{component}-{version}-1.fc{fedora}'])
        if "No such build" in res:
            print(f"🙈     Fedora {fedora}: No Koji build for {component} {version}")
            releases.add(fedora)
        elif "State: FAILED" in res:
            msg_error(f"Fedora {fedora}: Build for {component} {version} failed")
        else:
            print(f"      Fedora {fedora}: {component} {version} in Koji")

        res = run_command(['bodhi', 'updates', 'query', '--builds', f'{component}-{version}-1.fc{fedora}'])
        if "0 updates found" in res:
            print(f"🙈     Fedora {fedora}: No Bodhi update for {component} {version}")
            releases.add(fedora)
            updates.add(fedora)
        else:
            print(f"      Fedora {fedora}: {component} {version} in Bodhi")

    return list(releases), list(updates)


def get_fedora_releases():
    """Get all active Fedora releases"""
    # https://github.com/sgallagher/get-fedora-releases-action/blob/main/get_fedora_releases.py
    res = None
    while not res:
        try:
            res = requests.get('https://bodhi.fedoraproject.org/releases?state=current')
        except requests.exceptions.Timeout:
            pass
        except requests.exceptions.HTTPError as err:
            msg_error(err)

    #res.raise_for_status()

    stable = set()
    for release in res.json()['releases']:
        if release['id_prefix'] == "FEDORA":
            stable.add(release['version'])

    res = requests.get('https://bodhi.fedoraproject.org/releases?state=pending')
    res.raise_for_status()

    devel = set()
    for release in res.json()['releases']:
        if release['id_prefix'] == "FEDORA" and release['version'] != "eln":
            devel.add(release['version'])

    return list(devel.union(stable))


def main():
    """Main function"""
    components = ['osbuild','osbuild-composer','koji-osbuild']

    parser = argparse.ArgumentParser()
    parser.add_argument("-u", "--user", help="Set the username of the Fedora account")
    parser.add_argument("-p", "--password", help="Set the Fedora account password")
    parser.add_argument("--apikey", help="Set the Fedora account API key")
    args = parser.parse_args()

    fedoras = get_fedora_releases()

    for component in components:
        msg_info(f"Checking for open pull requests of {component}...")
        merge_open_pull_requests(args, component)

        msg_info(f"Checking for missing builds of '{component}'...")
        missing_builds, missing_updates = check_release_builds(component, fedoras)

        if missing_builds or missing_updates:
            msg_info(f"Found missing builds in Koji: {missing_builds}")
            msg_info(f"Found missing updates in Bodhi: {missing_updates}")
            kinit(args)
            schedule_fedora_builds(args, component, missing_builds, missing_updates)
            msg_ok(f"Tried to schedule builds for {missing_builds} and update {missing_updates}.")
        else:
            msg_ok("No releases found with missing builds. Exiting.")


if __name__ == "__main__":
    main()
