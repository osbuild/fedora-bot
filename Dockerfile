FROM registry.fedoraproject.org/fedora:36

RUN dnf -y install --setopt=install_weak_deps=False \
  krb5-workstation fedpkg koji bodhi-client \
  python3 python3-pexpect python3-cryptography python3-slackclient && \
  dnf clean all

ENV KRB5CCNAME=/tmp/ticket
