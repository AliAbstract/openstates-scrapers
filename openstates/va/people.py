import re
import pytz
import datetime

import lxml.html
from pupa.scrape import Person
from pupa.scrape import Scraper
from pupa.scrape import Organization
from spatula import Page, Spatula

from .common import SESSION_SITE_IDS


CHAMBER_MOVES = {
    "A. Benton \"Ben\" Chafin-Elect": "upper",
    "A. Benton Chafin-Senate Elect": "upper",
}
PARTY_MAP = {
    'R': 'Republican',
    'D': 'Democratic',
    'I': 'Independent',
}
TIMEZONE = pytz.timezone('US/Eastern')


class MemberDetail(Page):
    list_xpath = '//body'

    def handle_list_item(self, item):
        party_district_text = item.xpath('//h3/font/text()')[0]
        party, district = get_party_district(party_district_text)
        self.obj.add_term(self.role, self.chamber, district=district)
        self.obj.add_party(PARTY_MAP[party])

        photo_url = self.get_photo_url()
        if photo_url is not None:
            self.obj.image = photo_url

        for com in item.xpath('//ul[@class="linkSect"][1]/li/a/text()'):
            org = Organization(
                name=com,
                chamber=self.chamber,
                classification='committee',
            )
            org.add_source(self.url)
            yield org

            self.obj.add_membership(
                org,
                start_date=maybe_date(self.kwargs['session'].get('start_date')),
                end_date=maybe_date(self.kwargs['session'].get('end_date')),
            )

    def get_photo_url(self):
        pass


class SenateDetail(MemberDetail):
    role = 'Senator'
    chamber = 'upper'

    def get_photo_url(self):
        lis_id = get_lis_id(self.chamber, self.url)
        profile_url = 'http://apps.senate.virginia.gov/Senator/memberpage.php?id={}'.format(lis_id)
        page = lxml.html.fromstring(self.scraper.get(profile_url).text)
        src = page.xpath('.//img[@class="profile_pic"]/@src')
        return src[0] if src else None


class DelegateDetail(MemberDetail):
    role = 'Delegate'
    chamber = 'lower'

    def get_photo_url(self):
        lis_id = get_lis_id(self.chamber, self.url)
        if lis_id:
            lis_id = '{}{:04d}'.format(lis_id[0], int(lis_id[1:]))
            return (
                'http://memdata.virginiageneralassembly.gov'
                '/images/display_image/{}'
            ).format(lis_id)


class MemberList(Page):
    def handle_list_item(self, item):
        name = item.text

        if 'resigned' in name.lower() or 'vacated' in name.lower():
            return
        if (name in CHAMBER_MOVES and(self.chamber != CHAMBER_MOVES[name])):
            return

        name, action, date = clean_name(name)

        leg = Person(name=name)
        leg.add_source(self.url)
        leg.add_source(item.get('href'))
        leg.add_link(item.get('href'))
        yield from self.scrape_page(
            self.detail_page,
            item.get('href'),
            session=self.kwargs['session'],
            obj=leg,
        )
        yield leg


party_district_pattern = re.compile(r'\((R|D|I)\) - (?:House|Senate) District\s+(\d+)')


def get_party_district(text):
    return party_district_pattern.match(text).groups()


lis_id_patterns = {
    'upper': re.compile(r'(S[0-9]+$)'),
    'lower': re.compile(r'(H[0-9]+$)'),
}


def get_lis_id(chamber, url):
    """Retrieve LIS ID of legislator from URL."""
    match = re.search(lis_id_patterns[chamber], url)
    if match.groups:
        return match.group(1)


name_elect_pattern = re.compile(r'(- Elect)$')
name_resigned_pattern = re.compile(r'-(Resigned|Member) (\d{1,2}/\d{1,2})?')


def clean_name(name):
    name = name_elect_pattern.sub('', name).strip()
    action, date = (None, None)
    match = re.search(r'-(Resigned|Member) (\d{1,2}/\d{1,2})?', name)
    if match:
        action, date = match.groups()
        name = name.rsplit('-')[0]
    return name, action, date


class SenateList(MemberList):
    chamber = 'upper'
    detail_page = SenateDetail
    list_xpath = '//div[@class="lColRt"]/ul/li/a'


class DelegateList(MemberList):
    chamber = 'lower'
    detail_page = DelegateDetail
    list_xpath = '//div[@class="lColLt"]/ul/li/a'


class VaPersonScraper(Scraper, Spatula):
    def scrape(self, session=None):
        if not session:
            session = self.jurisdiction.legislative_sessions[-1]
            self.info('no session specified, using %s', session['identifier'])
        url = 'http://lis.virginia.gov/{}/mbr/MBR.HTM'.format(
            SESSION_SITE_IDS[session['identifier']]
        )
        yield from self.scrape_page_items(SenateList, session=session, url=url)
        yield from self.scrape_page_items(DelegateList, session=session, url=url)


def maybe_date(text):
    try:
        date = datetime.datetime.strptime(text, '%Y-%d-%m')
        return TIMEZONE.localize(date)
    except ValueError:
        return ''
