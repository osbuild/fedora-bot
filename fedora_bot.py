#!/usr/bin/python3

"""Just a small bot to take care of Koji builds and Bodhi updates"""

import argparse
import subprocess
import sys
import os
import re
import pexpect
import requests
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

    response = webhook.send(text=f'<{github_url}|fedora-bot>: {message}')
    assert response.status_code == 200
    assert response.body == "ok"


def update_bodhi(args,component,fedora):
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
    if "completed successfully" in res:
        slack_notify(f"Bodhi updated for {fedora}.")


def schedule_fedora_builds(args,component,fedoras,missing_updates):
    """Schedule builds for all active Fedora releases with missing builds"""

    if component == "osbuild":
        url = "https://koji.fedoraproject.org/koji/packageinfo?packageID=29756"
    elif component == "osbuild-composer":
        url = "https://koji.fedoraproject.org/koji/packageinfo?packageID=31032"

    work_dir = os.getcwd()

    os.chdir(os.path.join(work_dir, component))

    for fedora in fedoras:
        path = os.getcwd()
        msg_info(f"Scheduling build for Fedora {fedora} (this may take a while)")
        if fedora == '36':
            branch = "rawhide"
        else:
            branch = f"f{fedora}"
        res = run_command(['git', 'checkout', branch])
        print(f"      Checked out branch '{branch}'")

        res = run_command(['fedpkg', 'build'])
        print(res)

        if "completed successfully" in res:
            slack_notify(f"<{url}|Koji build> for {fedora} completed successfully.")

            if fedora != "rawhide":
                update_bodhi(args,component,fedora)
        elif fedora in missing_updates:
            if fedora != "rawhide":
                update_bodhi(args,component,fedora)
        else:
            msg_info(f"Did not build {fedora}.")
            continue

    msg_info(f"Check {url} for all {component} builds.")


def get_latest_dist_git_release(component):
    """Get the latest release version found in dist-git"""
    msg_info(f"Cloning into 'https://src.fedoraproject.org/rpms/{component}.git'...")
    work_dir = os.getcwd()
    res = run_command(['git','clone',f"https://src.fedoraproject.org/rpms/{component}.git"])
    print(res)
    os.chdir(os.path.join(work_dir, component))
    branch = run_command(['git','branch','--show-current'])
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


def check_release_builds(component,fedoras):
    """Check if there are missing builds in Koji for any active Fedora release"""
    version = get_latest_dist_git_release(component)
    print(f"      Version {component} {version} found in dist-git")

    releases = set()
    updates = set()

    for fedora in fedoras:
        res = run_command(['koji','buildinfo',f'{component}-{version}-1.fc{fedora}'])
        if "No such build" in res:
            print(f"🙈     Fedora {fedora}: No Koji build for {component} {version}")
            releases.add(fedora)
        elif "State: FAILED" in res:
            msg_error(f"Fedora {fedora}: Build for {component} {version} failed")
        else:
            print(f"      Fedora {fedora}: {component} {version} in Koji")

        res = run_command(['bodhi','updates','query','--builds',f'{component}-{version}-1.fc{fedora}'])
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
    components = ['osbuild','osbuild-composer']

    parser = argparse.ArgumentParser()
    parser.add_argument("-u", "--user", help="Set the username of the Fedora account")
    parser.add_argument("-p", "--password", help="Set the Fedora account password")
    args = parser.parse_args()

    fedoras = get_fedora_releases()

    for component in components:
        msg_info(f"Checking for missing builds of '{component}'...")
        missing_builds, missing_updates = check_release_builds(component,fedoras)

        if missing_builds or missing_updates:
            msg_info(f"Found missing builds in Koji: {missing_builds}")
            msg_info(f"Found missing updates in Bodhi: {missing_updates}")
            kinit(args)
            schedule_fedora_builds(args,component,missing_builds,missing_updates)
            msg_ok(f"Tried to schedule builds for {missing_builds} and update {missing_updates}.")
        else:
            msg_ok("No releases found with missing builds. Exiting.")


if __name__ == "__main__":
    main()
