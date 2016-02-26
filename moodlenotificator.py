import requests
import json
import datetime
import smtplib
import logging
import sys
import configparser
from email.mime.text import MIMEText


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
            disc_list.append((item['firstuserfullname'], datetime.datetime.fromtimestamp(item['timemodified']), item['subject'],))
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
                for name, tim, subject in discussions:
                    if tim >= datetime.datetime.today() - datetime.timedelta(days=1):
                        #print(subject)
                        for userid, email, fullname in users:
                            if userid in collector:
                                collector[userid].append((course, subject, cname,))
                            else:
                                collector[userid] = [(course, subject, cname,)]
                            if userid not in usercol:
                                usercol[userid] = (email, fullname,)
                                msgcol[userid] = self.Moo.get_messages()
            for userid, email, fullname in users:
                if userid not in usercol:
                    usercol[userid] = (email, fullname,)
                    msgcol[userid] = self.Moo.get_messages(userid)
        return collector, usercol, msgcol

    def send_mails(self, coll, ucoll, mcoll):
        for userid in ucoll:
            if userid in coll or (userid in mcoll and (len(mcoll[userid]) > 0)):
                txt = self.prepare_txt(userid, coll, mcoll)
                email, fullname = ucoll[userid]
                print(email, fullname, txt)

    def prepare_txt(self, userid, coll, mcoll):
        txt = ''
        if userid in coll and len(coll[userid]) > 0:
            txt = '\nNeue Forumseinträge: \n'
            for item in coll[userid]:
                txt += 'Kurs: {} Subject: {} \n'.format(item[2], item[1])
                #print(item)
        if userid in mcoll and len(mcoll[userid]) > 1:
            txt += '\nSie haben {} ungelesene Nachrichten.'.format(len(mcoll[userid]))
        elif userid in mcoll and len(mcoll[userid]) > 0:
            txt += '\nSie haben eine ungelesene Nachricht.'
        return txt + '\n\n'

    def check_for_new_mails(self):
        #self.Mailer.connect()
        for userid in self.users:
            mails = get_email_text(self.get_mails(userid))
            if len(mails) > 0:
                for m in mails:
                    if m[4] > self.users[userid][1]:
                        ##print('Sende Mail', m)
                        ##self.Mailer.connect()
                        #self.Mailer.send(m[3], m[1], m[2], self.users[userid][0], m[4])
                        print(m[3], m[1], m[2])
                        ##self.Mailer.quit()
                self.users[userid][1] = mails[0][4]
        #self.Mailer.quit()


def get_email_address(user):
    if len(user['users']) > 0:
        return user['users'][0]['id'], user['users'][0]['email']
    else:
        return None


def get_email_text(mails):
    mails_coll = list()
    for mail in mails['messages']:
        mails_coll.append( (mail['id'], mail['userfromfullname'], mail['usertofullname'], mail['subject'], datetime.datetime.fromtimestamp(mail['timecreated'])))
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
            logging.CRITICAL('Could not connect to SMTP. Err: {}'.format(err))
            sys.exit(-1)

    def send(self, to_name, to_email, txt, subject='WIB Moodle Notification',):
        msg = MIMEText(txt)
        msg.set_charset('utf8')
        msg['Subject'] = subject
        msg['From'] = 'WIB Lernsystem<{}>'.format(self.sender_emailaddress)
        msg['To'] = '{}<{}>'.format(to_name, to_email)
        #print(msg.as_string())
        logging.DEBUG('Message sent to {}'.format(to_name))
        try:
            self.server.send_message(msg, self.sender_emailaddress, to_email)
        except smtplib.SMTPRecipientsRefused as err:
            logging.WARNING('Tried to send mail. Recipient refused. Err: {}'.format(err))

    def quit(self):
        self.server.quit()

if __name__ == '__main__':
    logging.basicConfig(format='%(levelname)s - %(asctime)s - %(message)s',
                        filename='moodle_notificator.log',
                        filemode='w',
                        level=logging.DEBUG)

    #write_config()

    n = Notificator()

    c, u, m = n.fetch()
    n.send_mails(c, u, m)
    '''
    config = configparser.ConfigParser()
    config.read('config.ini')
    mailerconf = config['rsdk.net']
    moodleconf = config['wib-lehre']

    Mailer = Mailer(
            mailerconf['mail_smtp_url'],
            mailerconf['mail_smtp_port'],
            mailerconf['mail_username'],
            mailerconf['mail_password'],
            mailerconf['mail_sender_emailaddress']
        )

    Moo = MoodleGetter(
            moodleconf['moodle_resturl'],
            moodleconf['moodle_wstoken']
    )


    print(Moo.core_message_get_messages(26))

    '''
    '''
    txt = "New Message from {}\n\nSubject: {}\nTimestamp: {}\n" \
              "More Information: " \
              "https://wib-lehre.htw-aalen.de/lernsystem".format(from_name, subject, dt)


    #result = m.core_course_get_courses()
    result = m.get_courseid_list()
    for item in result:
        print(item)


    #result_c = m.mod_forum_get_forums_by_courses(3)
    result_c = m.get_forumid_list(20)
    print(result_c)


    #result_e = m.core_enrol_get_enrolled_users()
    result_e = m.get_course_user_list(2)
    print(result_e)



    #result_f = m.mod_forum_get_forum_discussions(34)
    result_f = m.get_disc_list(34)
    print(result_f)


    n = Notificator()
    print(n.users)
    sleeptime = 60 * 15


    while True:
        logging.DEBUG('Sleeping for {} minutes.'.format(sleeptime / 60))
        time.sleep(sleeptime)
        logging.INFO('checking for new Mail')
        n.check_for_new_mails()
    '''
