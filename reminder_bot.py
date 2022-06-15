#!/usr/bin/python3
import os
import sys
import argparse
import yaml
import requests
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
    """
    Returns all Wednesdays of a given year
    """
    d = date(year, 1, 1)
    d += timedelta(days = 2 - d.weekday())
    if d.year != year:
        d += timedelta(days = 2 - d.weekday() + 7)
    
    while d.year == year:
        yield d
        d += timedelta(days = 7)


def create_yearly_plan(components, year: int):
    """
    Generate a yaml file holding an empty release plan for a component (every second Wednesday)
    """
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
    """
    Reads the release schedule yaml file and returns it
    """
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
    """
    Sends a Slack notification to the #osbuild-release channel
    """
    url = os.getenv('SLACK_WEBHOOK_URL')
    github_server_url = os.getenv('GITHUB_SERVER_URL')
    github_repository = os.getenv('GITHUB_REPOSITORY')
    github_run_id = os.getenv('GITHUB_RUN_ID')
    github_url = f"{github_server_url}/{github_repository}/actions/runs/{github_run_id}"

    print(message)

    if url:
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
                internal_guide = "https://osbuild.pages.redhat.com/internal-guides/releasing.html"
                instructions = (f"*1.* Watch this channel for the CS9/RHEL9 merge requests (<{internal_guide}|read the docs>)\n"
                                "*2.* Once everything is green, merge the CS9 merge request and watch this channel for the RHEL8 pull request.\n")
                slack_notify(f'{message} <https://github.com/osbuild/{component}/releases|{component} release> by {foreperson}\n{instructions}')

    if overview:
        overview.sort()
        slack_notify(f"{message}\n{''.join(overview)}")


def frontend_reminder():
    """
    Checks if there are dependabot PRs open against the frontend for >7 days
    """
    repo = 'image-builder-frontend'
    owner = 'RedHatInsights'
    days = 7
    one_week = date.today() - timedelta(days = days)

    payload = { "q":f'type:pr is:open repo:{owner}/{repo} label:dependencies created:<{one_week}' }

    print(payload)
    try:
        req = requests.get("https://api.github.com/search/issues", params=payload)
        res = req.json()
    except:
        print(f"Couldn't get PR infos for {owner}/{repo}.")
        res = None

    if req.status_code == 200 and res is not None:
        items = res["items"]
        pull_requests = ""

        if len(items) > 0:
            for item in items:
                print(f"{item['html_url']}")
                pull_requests += f" *<{item['html_url']}|#{item['number']}>*"

            text = "pull requests have"
            if len(items) == 1:
                text = "pull request has"
            slack_notify(f"{len(items)} dependabot {text} been open for more than {days} days:{pull_requests}")


if __name__ == "__main__":
    """Main function"""
    components = ['osbuild-composer','osbuild']

    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("-y", "--year", help="Year to use to create yaml files for osbuild and osbuild-composer")
    parser.add_argument("-m", "--monthly", help="Send the monthly overview to our #osbuild-release Slack channel",
                        default=False, action=argparse.BooleanOptionalAction)
    parser.add_argument("-r", "--reminder", help="Send the release reminders on the scheduled date to our #osbuild-release Slack channel",
                        default=False, action=argparse.BooleanOptionalAction)
    parser.add_argument("-f", "--frontend", help="Check open PRs against frontend and ping on Slack",
                        default=False, action=argparse.BooleanOptionalAction)
    args = parser.parse_args()

    if args.year is not None:
        create_yearly_plan(components, int(args.year))

    key = os.getenv('SLACK_NICKS_KEY')
    if key:
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

    if args.frontend is True and date.weekday(date.today()) == 0:                         # send frontend reminders only on Mondays
        frontend_reminder()