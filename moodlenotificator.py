#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import configparser
import datetime
import json
import logging
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from jinja2 import Template


class MoodleGetter:
    def __init__(self, resturl, wstoken):
        self.resturl = resturl
        self.userlist = None
        self.wstoken = wstoken

    def get_json_from_moodle(self, payload):
        payload['wstoken'] = self.wstoken
        payload['moodlewsrestformat'] = 'json'
        r = requests.get(self.resturl, params=payload)
        if not r.status_code == requests.codes.ok:
            logging.error(r.status_code)
            sys.exit(1)
        resp = json.loads(r.text)
        if 'exception' in resp:
            print(resp['exception'], '  ', resp['errorcode'], '  ', resp['message'])
            logging.error("Exception: {}  Errorcode: {}  Message {} mit Payload: {}".format(
                resp['exception'],
                resp['errorcode'],
                resp['message'],
                str(payload)
            ))
            sys.exit(2)
        return resp

    def core_course_get_courses(self):
        payload = {
            'wsfunction': 'core_course_get_courses'
        }
        return self.get_json_from_moodle(payload)

    def get_courseid_list(self):
        '''
        example output:
        (1, 'Master WIB HS-Aalen')
        (2, 'Dienstleistungsmanagement')
        :return:
        '''
        courseid_list = list()
        full_list = self.core_course_get_courses()
        for item in full_list:
            courseid_list.append((item['id'], item['fullname'],))
        return courseid_list

    def mod_forum_get_forums_by_courses(self, courseid=2):
        payload = {
            'wsfunction': 'mod_forum_get_forums_by_courses',
            'courseids[0]': courseid
        }
        return self.get_json_from_moodle(payload)

    def get_forumid_list(self, courseid):
        forumid_list = list()
        full_list = self.mod_forum_get_forums_by_courses(courseid)
        for item in full_list:
            forumid_list.append((item['course'], item['type'], item['id'],))
        return forumid_list

    def mod_forum_get_forum_discussions(self, forumid=2):
        payload = {
            'wsfunction': 'mod_forum_get_forum_discussions',
            'forumids[0]': forumid
        }
        return self.get_json_from_moodle(payload)

    def get_disc_list(self, forumid):
        disc_list = list()
        full_list = self.mod_forum_get_forum_discussions(forumid)
        for item in full_list:
            disc_list.append(
                (item['firstuserfullname'], datetime.datetime.fromtimestamp(item['timemodified']), item['subject'],))
        return disc_list

    def core_enrol_get_enrolled_users(self, courseid=2):
        payload = {
            'wsfunction': 'core_enrol_get_enrolled_users',
            'courseid': courseid
        }
        return self.get_json_from_moodle(payload)

    def get_course_user_list(self, courseid):
        cuser_list = list()
        full_list = self.core_enrol_get_enrolled_users(courseid)
        for item in full_list:
            cuser_list.append((item['id'], item['email'], item['fullname'],))
        return cuser_list

    def get_user_info(self, userid):
        payload = {
            'wsfunction': 'core_user_get_users',
            'criteria[0][key]': 'id',
            'criteria[0][value]': userid
        }
        return self.get_json_from_moodle(payload)

    def core_message_get_messages(self, userid):
        payload = {
            'wsfunction': 'core_message_get_messages',
            'read': 0,  # 1 gelesen  # 0 ungelesen
            'newestfirst': 1,
            'limitnum': 10,
            'useridto': userid
        }
        return self.get_json_from_moodle(payload)

    def get_messages(self, userid):
        msg = self.core_message_get_messages(userid)
        return msg['messages']


class Notificator:
    def __init__(self):
        self.user_list = None
        self.config = configparser.ConfigParser()
        self.config.read('config.ini')
        self.mailerconf = self.config['rsdk.net']
        self.moodleconf = self.config['wib-lehre']
        self.Mailer = Mailer(
            self.mailerconf['mail_smtp_url'],
            self.mailerconf['mail_smtp_port'],
            self.mailerconf['mail_username'],
            self.mailerconf['mail_password'],
            self.mailerconf['mail_sender_emailaddress']
        )
        self.Moo = MoodleGetter(
            self.moodleconf['moodle_resturl'],
            self.moodleconf['moodle_wstoken']
        )

    def fetch(self):
        courses = self.Moo.get_courseid_list()
        collector = dict()
        usercol = dict()
        msgcol = dict()
        for courseid, cname in courses:
            # In diesem Kurs
            # User
            users = self.Moo.get_course_user_list(courseid)
            # Forum
            forums = self.Moo.get_forumid_list(courseid)
            for course, typ, forumid in forums:
                discussions = self.Moo.get_disc_list(forumid)
                for dname, tim, subject in discussions:
                    if tim >= datetime.datetime.today() - datetime.timedelta(days=1):
                        # print(subject)
                        for userid, email, fullname in users:
                            if userid in collector:
                                collector[userid].append((course, subject, cname, dname,))
                            else:
                                collector[userid] = [(course, subject, cname, dname,)]
                            if userid not in usercol:
                                usercol[userid] = (email, fullname,)
                                msgcol[userid] = self.Moo.get_messages()
            for userid, email, fullname in users:
                if userid not in usercol:
                    usercol[userid] = (email, fullname,)
                    msgcol[userid] = self.Moo.get_messages(userid)
        return collector, usercol, msgcol

    def send_mails(self, coll, ucoll, mcoll):
        with open('mail-template.html', encoding='utf-8') as f:
            template_html = Template(f.read())
        with open('mail-template.txt', encoding='utf-8') as f:
            template_txt = Template(f.read())
        self.Mailer.connect()
        for userid in ucoll:
            if userid not in [99]:
                if userid in coll or (userid in mcoll and (len(mcoll[userid]) > 0)):
                    txt, html = self.prepare_txt(userid, coll, mcoll, template_html, template_txt, ucoll)
                    email, fullname = ucoll[userid]
                    print(email, userid, fullname, html, txt)
                    self.Mailer.send(fullname, email, txt, html)
        self.Mailer.quit()

    def prepare_txt(self, userid, coll, mcoll, template_html, template_txt, ucoll):
        forumslist = list()
        if userid in coll and len(coll[userid]) > 0:
            for item in coll[userid]:
                forumslist.append({'kursname': item[2], 'subject': item[1], 'username': item[3]})
        txt_msgs = None
        if userid in mcoll and len(mcoll[userid]) > 1:
            txt_msgs = '{} ungelesene Nachrichten'.format(len(mcoll[userid]))
        elif userid in mcoll and len(mcoll[userid]) > 0:
            txt_msgs = 'eine ungelesene Nachricht'
        _, fullname = ucoll[userid]
        html = template_html.render(name=fullname, msgs=txt_msgs, foren=forumslist)
        txt = template_txt.render(name=fullname, msgs=txt_msgs, foren=forumslist)
        return txt, html


def get_email_address(user):
    if len(user['users']) > 0:
        return user['users'][0]['id'], user['users'][0]['email']
    else:
        return None


def get_email_text(mails):
    mails_coll = list()
    for mail in mails['messages']:
        mails_coll.append((mail['id'], mail['userfromfullname'], mail['usertofullname'], mail['subject'],
                           datetime.datetime.fromtimestamp(mail['timecreated'])))
    return mails_coll


class Mailer:
    def __init__(self, smtp_url, smtp_port, username, password, sender_emailaddress):
        self.smtp_url = smtp_url
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.sender_emailaddress = sender_emailaddress
        self.server = None

    def connect(self):
        try:
            self.server = smtplib.SMTP(self.smtp_url, self.smtp_port)
            self.server.ehlo()
            self.server.starttls()
            self.server.ehlo()
            self.server.login(self.username, self.password)
        except smtplib.SMTPResponseException as err:
            logging.critical('Could not connect to SMTP. Err: {}'.format(err))
            sys.exit(-1)

    def send(self, to_name, to_email, txt, html, subject='WIB Lernsystem Notification', ):
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = 'WIB Lernsystem<{}>'.format(self.sender_emailaddress)
        msg['To'] = '{}<{}>'.format(to_name, to_email)
        # print(msg.as_string())
        msg.set_charset('utf8')
        part1 = MIMEText(txt, 'plain')
        part2 = MIMEText(html, 'html')
        msg.attach(part1)
        msg.attach(part2)
        logging.debug('Message sent to {}'.format(to_name))
        try:
            self.server.send_message(msg, self.sender_emailaddress, to_email)
        except smtplib.SMTPRecipientsRefused as err:
            logging.warning('Tried to send mail. Recipient refused. Err: {}'.format(err))

    def quit(self):
        self.server.quit()


if __name__ == '__main__':
    logging.basicConfig(format='%(levelname)s - %(asctime)s - %(message)s',
                        filename='moodle_notificator.log',
                        filemode='w',
                        level=logging.WARNING)

    n = Notificator()
    c, u, m = n.fetch()
    n.send_mails(c, u, m)
