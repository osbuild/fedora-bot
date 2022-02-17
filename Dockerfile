FROM quay.io/fedora/fedora:34

RUN dnf -y install krb5-workstation python3.10 python3-pip fedpkg koji --setopt=install_weak_deps=False
RUN pip install --no-cache-dir pexpect slack_sdk cryptography

ENV KRB5CCNAME=/tmp/ticket
