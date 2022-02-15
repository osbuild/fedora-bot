#!/usr/bin/python3
import os
import sys
import argparse
import yaml
from datetime import date, timedelta
from cryptography.fernet import Fernet
from slack_sdk.webhook import WebhookClient


def load_key(keyfile):
    """
    Loads the key from the current directory named `key.key`
    """
    return open(keyfile, "rb").read()


def decrypt(filename, key):
    """
    Given a filename (str) and key (bytes), it decrypts the file and write it
    """
    f = Fernet(key)
    with open(filename, "rb") as file:
        encrypted_data = file.read()

    decrypted_data = f.decrypt(encrypted_data)
    return decrypted_data.decode()


def all_wednesdays(year: int):
    d = date(year, 1, 1)
    d += timedelta(days = 2 - d.weekday())
    if d.year != year:
        d += timedelta(days = 2 - d.weekday() + 7)
    
    while d.year == year:
        yield d
        d += timedelta(days = 7)


def create_yearly_plan(components, year: int):
    for component in components:
        with open(f'{year}-{component}.yaml','w') as file:
            i = components.index(component)
            lines = ""
            for d in all_wednesdays(year):
                if i % 2:
                    lines += f"{d}: \n"
                i += 1
            file.write(lines)


def release_schedule(component: str):
    d = date.today()
    filename = f'{d.year}-{component}.yaml'
    if os.path.isfile(filename):
        with open(filename,'r') as file:
            release_dates = yaml.load(file, Loader=yaml.FullLoader)
    else:
        print(f"Error. The {filename} was not found. Please create it first by running 'python3 reminder_bot.py --year'.")
        sys.exit(1)

    return release_dates


def slack_notify(message: str):
    url = os.getenv('SLACK_WEBHOOK_URL')
    github_server_url = os.getenv('GITHUB_SERVER_URL')
    github_repository = os.getenv('GITHUB_REPOSITORY')
    github_run_id = os.getenv('GITHUB_RUN_ID')
    github_url = f"{github_server_url}/{github_repository}/actions/runs/{github_run_id}"

    print(message)

    webhook = WebhookClient(url)

    response = webhook.send(
        text="fallback",
        blocks=[
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"<{github_url}|reminder-bot>: :loudspeaker: {message}"
                }
            }
        ])
    assert response.status_code == 200
    assert response.body == "ok"


def send_reminder(components, slack_nicks, target_date, message: str):
    """
    Reads the release overviews and sends reminders appropriately
    """
    overview = []

    for component in components:
        releases = release_schedule(component)

        for release_date, foreperson in releases.items():
            if foreperson:
                for name, userid in slack_nicks.items():
                    if foreperson in name:
                        foreperson = f"<@{userid}>"

            if (target_date == date.today().month and target_date == release_date.month):
                overview.append(f"{release_date}: {component} release by {foreperson}\n")
            elif release_date == target_date:
                dev_guide = "https://www.osbuild.org/guides/developer-guide/releasing.html"
                internal_guide = "https://osbuild.pages.redhat.com/internal-guides/releasing.html"
                instructions = (f"*1.* Create an upstream release (<{dev_guide}|read the docs>)\n"
                                f"*2.* Watch this channel for the CS9/RHEL9 merge requests (<{internal_guide}|read the docs>)\n"
                                "*3.* Once everything is green, merge the CS9 merge request and watch this channel for the RHEL8 pull request.\n"
                                "In between, either enjoy the update messages from Koji, Bodhi and Brew or have some :popcorn:")
                slack_notify(f'{message} <https://github.com/osbuild/{component}/releases|{component} release> by {foreperson}\n{instructions}')

    if overview:
        overview.sort()
        slack_notify(f"{message}\n{''.join(overview)}")


if __name__ == "__main__":
    """Main function"""
    components = ['osbuild-composer','osbuild']
    url = os.getenv('SLACK_WEBHOOK_URL')

    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("-y", "--year", help="Year to use to create yaml files for osbuild and osbuild-composer")
    parser.add_argument("-m", "--monthly", help="Send the monthly overview to our #osbuild-release Slack channel",
                        default=False, action=argparse.BooleanOptionalAction)
    parser.add_argument("-r", "--reminder", help="Send the release reminders on the scheduled date to our #osbuild-release Slack channel",
                        default=False, action=argparse.BooleanOptionalAction)
    args = parser.parse_args()

    if args.year is not None:
        create_yearly_plan(components, int(args.year))

    key = os.getenv('SLACK_NICKS_KEY')
    slack_nicks = yaml.safe_load(decrypt("slack_nicks_encrypted.yaml", key))

    if args.reminder is True:
        wednesday = date.today() - timedelta(days=2)
        message = f"*This Wednesday* ({wednesday}) we have scheduled an"
        send_reminder(components, slack_nicks, date.today() + timedelta(days=2), message) # send reminder on Monday before the release
        message = "*Today* we have scheduled an"
        send_reminder(components, slack_nicks, date.today(), message)                     # send a reminder on the release day

    if args.monthly is True:
        message = f":rocket: *Upcoming releases for {' and '.join(components)}* :rocket:"
        send_reminder(components, slack_nicks, date.today().month, message)               # send an overview on the first of the month
