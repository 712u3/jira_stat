import dateutil.parser as dp
from datetime import datetime, date, time, timedelta

def start_of_day(dt):
    return dt.replace(hour=0, minute=0, second=0)

def end_of_day(dt):
    new_dt = dt + timedelta(days=1)
    return new_dt.replace(hour=0, minute=0, second=0)

def norm_datetime_parser(datetime_string):
    dt = dp.parse(datetime_string)
    dt = dt - dt.utcoffset()
    return datetime(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)

def get_issue_links(issue):
    return [
        {
            'type': link['type']['inward'],
            'key': link['outwardIssue']['key'] if link.get('inwardIssue', None) == None \
                else link['inwardIssue']['key']
        }
        for link in issue.raw['fields']['issuelinks']
    ]

def get_issue_flags(issue):
    raw = [
        {
            'author': history.author.name,
            'date': norm_datetime_parser(history.created),
            'from': 'Unflagged' if item.toString == 'Impediment' else 'Flagged',
            'to': 'Flagged' if item.toString == 'Impediment' else 'Unflagged'
        }
        for history in issue.changelog.histories
            for item in history.items
                if item.field == 'Flagged'
    ]
    
    result = []
    for i in range(len(raw)):
        if i%2 == 0: 
            if raw[i]['from'] != 'Unflagged' or raw[i]['to'] != 'Flagged':
                raise Exception('flag history corrupted')

            result.append({
                'author': raw[i]['author'],
                'start': raw[i]['date']
            })
        else:
            if raw[i]['from'] != 'Flagged' or raw[i]['to'] != 'Unflagged':
                raise Exception('flag history corrupted')
            
            result[-1]['end'] = raw[i]['date']
            
    return result

def get_issue_transitions(issue):
    return [
        {
            'author': history.author.name,
            'date': norm_datetime_parser(history.created),
            'from': item.fromString,
            'to': item.toString
        }
        for history in issue.changelog.histories
            for item in history.items
                if item.field == 'status'
    ]

def get_issue_status_history(issue, transitions):
    status_history = [{
        'author': issue.fields.reporter.name,
        'status': transitions[0]['from'],
        'start': norm_datetime_parser(issue.fields.created),
        'end': transitions[0]['date']
    }]
    
    for i in range(len(transitions)-1):
        status_history.append({
            'author': transitions[i+1]['author'],
            'status': transitions[i]['to'],
            'start': transitions[i]['date'],
            'end': transitions[i+1]['date']
        })
        
    status_history.append({
        'author': issue.fields.assignee.name,
        'status': transitions[-1]['to'],
        'start': transitions[-1]['date'],
        'end': datetime.now()
    })
    
    return status_history

def enrich_history(history, holidays, vacations, flags):
    def date_arrange(start_d, end_d):
        return [
            start_d + timedelta(days=i) for i in range((end_d - start_d).days + 1)
        ]
    
    def remove_weekends(data_range):
        return [day for day in data_range if day.weekday() < 5]
    
    def remove_holidays(data_range):
        return [day for day in data_range if day not in holidays]
    
    def remove_vacations(data_range, vacations):
        def not_in_any_interval(day):
            for interval in vacations:
                if day >= interval[0] and day <= interval[1]:
                    return False
            return True
        
        if not vacations:
            return data_range
        
        return [day for day in data_range if not_in_any_interval(day)]
    
    def get_real_start_dt(start_dt, day):
        if start_dt.date() < day:
            return start_of_day(datetime.combine(day, time(0, 0)))
        elif start_dt.date() > day:
            raise Exception('start time out of range')
        else:
            return start_dt
    
    def get_real_end_dt(end_dt, day):
        if end_dt.date() > day:
            return end_of_day(datetime.combine(day, time(0, 0)))
        elif end_dt.date() < day:
            raise Exception('end time out of range')
        else:
            return end_dt
        
    def skoka_vychest_za_flagi(start_dt, end_dt):
        result = 0
        
        for flag_interval in flags:
            if (flag_interval['start'] >= start_dt and flag_interval['start'] < end_dt) \
                or (flag_interval['end'] >= start_dt and flag_interval['end'] < end_dt):

                fi_s = max(flag_interval['start'], start_dt)
                fi_e = min(flag_interval['end'], end_dt)

                result += (fi_e - fi_s).seconds // 60
            
        return result
    
    def sum_work_time(start_d, end_d, work_days):
        result = 0
        
        for day in work_days:
            start_dt = get_real_start_dt(start_d, day)
            end_dt = get_real_end_dt(end_d, day)
            
            result += (end_dt - start_dt).seconds // 60
            result -= skoka_vychest_za_flagi(start_dt, end_dt)
        
        return result
    
    for item in history[:-1]:
        work_days = date_arrange(item['start'].date(), item['end'].date())
        work_days = remove_weekends(work_days)
        work_days = remove_holidays(work_days)
        work_days = remove_vacations(work_days, vacations.get(item['author'], []))
        
        item['work_time_minutes'] = sum_work_time(item['start'], item['end'], work_days)
    
    history[-1]['work_time_minutes'] = 0
    return history

def get_develop_time(history):
    result = 0
    for item in history:
        if item['status'] in ('In Progress', 'Need Review'):
            result += item['work_time_minutes']
    return result

def get_test_time(history):
    result = 0
    for item in history:
        if item['status'] == 'Testing In Progress':
            result += item['work_time_minutes']
    return result

def get_wait_for_test_time(history):
    result = 0
    for item in history:
        if item['status'] == 'Need testing':
            result += item['work_time_minutes']
    return result

def parse(issue, holidays=[], vacations={}):
    tester = None if issue.raw['fields']['customfield_12622'] == None \
        else issue.raw['fields']['customfield_12622']['name']

    flags = get_issue_flags(issue)
    transitions = get_issue_transitions(issue)
    history = get_issue_status_history(issue, transitions)
    enrich_history(history, holidays, vacations, flags)
    
    develop_time = get_develop_time(history)
    test_time = get_test_time(history)
    wait_for_test_time = get_wait_for_test_time(history)
    
    return {
        'key': issue.key,
        'author': issue.fields.assignee.name,
        'develop_time': develop_time,
        'tester': tester,
        'test_time': test_time,
        'wait_for_test_time': wait_for_test_time,
        'resolution': issue.fields.resolution.name,
        'labels': issue.fields.labels,
        'links': get_issue_links(issue),
        'updated': norm_datetime_parser(issue.fields.updated),
        'resolved': norm_datetime_parser(issue.fields.resolutiondate),
        'history': history,
        'flags': flags
    }

