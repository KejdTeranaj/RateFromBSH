# -*- coding: utf-8 -*-

import datetime
from lxml import etree
from dateutil.relativedelta import relativedelta
import re
import logging
from pytz import timezone
import json

import requests

from datech import api, fields, models
from datech.exceptions import UserError
from datech.tools.translate import _
from datech.tools import DEFAULT_SERVER_DATE_FORMAT

bsh_URL = "https://www.bankofalbania.org/Tregjet/Kursi_zyrtar_i_kembimit"
MAP_CURRENCIES = {
    'US Dollar': 'USD',
    'UAE Dirham': 'AED',
    'Argentine Peso': 'ARS',
    'Australian Dollar': 'AUD',
    'Azerbaijan manat': 'AZN',
    'Bangladesh Taka': 'BDT',
    'Bulgarian lev': 'BGN',
    'Bahrani Dinar': 'BHD',
    'Bahraini Dinar': 'BHD',
    'Brunei Dollar': 'BND',
    'Brazilian Real': 'BRL',
    'Botswana Pula': 'BWP',
    'Belarus Rouble': 'BYN',
    'Canadian Dollar': 'CAD',
    'Swiss Franc': 'CHF',
    'Chilean Peso': 'CLP',
    'Chinese Yuan - Offshore': 'CNY',
    'Chinese Yuan': 'CNY',
    'Colombian Peso': 'COP',
    'Czech Koruna': 'CZK',
    'Danish Krone': 'DKK',
    'Algerian Dinar': 'DZD',
    'Egypt Pound': 'EGP',
    'Ethiopian birr': 'ETB',
    'Euro': 'EUR',
    'GB Pound': 'GBP',
    'Pound Sterling': 'GBP',
    'Hongkong Dollar': 'HKD',
    'Croatian kuna': 'HRK',
    'Hungarian Forint': 'HUF',
    'Indonesia Rupiah': 'IDR',
    'Israeli new shekel': 'ILS',
    'Indian Rupee': 'INR',
    'Iraqi dinar': 'IQD',
    'Iceland Krona': 'ISK',
    'Jordan Dinar': 'JOD',
    'Jordanian Dinar': 'JOD',
    'Japanese Yen': 'JPY',
    'Japanese Yen 100': 'JPY',
    'Kenya Shilling': 'KES',
    'Korean Won': 'KRW',
    'Kuwaiti Dinar': 'KWD',
    'Kazakhstan Tenge': 'KZT',
    'Lebanon Pound': 'LBP',
    'Sri Lanka Rupee': 'LKR',
    'Libyan dinar': 'LYD',
    'Moroccan Dirham': 'MAD',
    'Macedonia Denar': 'MKD',
    'Mauritian rupee': 'MUR',
    'Mexican Peso': 'MXN',
    'Malaysia Ringgit': 'MYR',
    'Nigerian Naira': 'NGN',
    'Norwegian Krone': 'NOK',
    'NewZealand Dollar': 'NZD',
    'Omani Rial': 'OMR',
    'Omani Riyal': 'OMR',
    'Peru Sol': 'PEN',
    'Philippine Piso': 'PHP',
    'Pakistan Rupee': 'PKR',
    'Polish Zloty': 'PLN',
    'Qatari Riyal': 'QAR',
    'Romanian leu': 'RON',
    'Serbian Dinar': 'RSD',
    'Russia Rouble': 'RUB',
    'Saudi Riyal': 'SAR',
    'Singapore Dollar': 'SGD',
    'Swedish Krona': 'SWK',
    'Syrian pound': 'SYP',
    'Thai Baht': 'THB',
    'Turkmen manat': 'TMT',
    'Tunisian Dinar': 'TND',
    'Turkish Lira': 'TRY',
    'Trin Tob Dollar': 'TTD',
    'Taiwan Dollar': 'TWD',
    'Tanzania Shilling': 'TZS',
    'Uganda Shilling': 'UGX',
    'Uzbekistani som': 'UZS',
    'Vietnam Dong': 'VND',
    'Yemen Rial': 'YER',
    'South Africa Rand': 'ZAR',
    'Zambian Kwacha': 'ZMW',
}

_logger = logging.getLogger(__name__)


def xml2json_from_elementtree(el, preserve_whitespaces=False):
    """ xml2json-direct
    Simple and straightforward XML-to-JSON converter in Python
    New BSD Licensed
    http://code.google.com/p/xml2json-direct/
    """
    res = {}
    if el.tag[0] == "{":
        ns, name = el.tag.rsplit("}", 1)
        res["tag"] = name
        res["namespace"] = ns[1:]
    else:
        res["tag"] = el.tag
    res["attrs"] = {}
    for k, v in el.items():
        res["attrs"][k] = v
    kids = []
    if el.text and (preserve_whitespaces or el.text.strip() != ''):
        kids.append(el.text)
    for kid in el:
        kids.append(xml2json_from_elementtree(kid, preserve_whitespaces))
        if kid.tail and (preserve_whitespaces or kid.tail.strip() != ''):
            kids.append(kid.tail)
    res["children"] = kids
    return res


# countries, provider_code, description
CURRENCY_PROVIDER_SELECTION = [
    ([], 'xe_com', 'xe.com'),
    (['AL'], 'bsh', 'Banka e Shqiperise'),
]

class ResCompany(models.Model):
    _inherit = 'res.company'

    currency_interval_unit = fields.Selection(
        selection=[
            ('manually', 'Manually'),
            ('daily', 'Daily'),
            ('weekly', 'Weekly'),
            ('monthly', 'Monthly')
        ],
        default='manually',
        required=True,
        string='Interval Unit',
    )
    currency_next_execution_date = fields.Date(string="Next Execution Date")
    currency_provider = fields.Selection(
        selection=[(provider_code, desc) for dummy, provider_code, desc in CURRENCY_PROVIDER_SELECTION],
        string='Service Provider',
        compute='_compute_currency_provider',
        readonly=False,
        store=True,
    )

    @api.depends('country_id')
    def _compute_currency_provider(self):
        code_providers = {
            country: provider_code
            for countries, provider_code, dummy in CURRENCY_PROVIDER_SELECTION
            for country in countries
        }
        for record in self:
            record.currency_provider = code_providers.get(record.country_id.code, 'ecb')

    def update_currency_rates(self):
        ''' This method is used to update all currencies given by the provider.
        It calls the parse_function of the selected exchange rates provider automatically.

        For this, all those functions must be called _parse_xxx_data, where xxx
        is the technical name of the provider in the selection field. Each of them
        must also be such as:
            - It takes as its only parameter the recordset of the currencies
              we want to get the rates of
            - It returns a dictionary containing currency codes as keys, and
              the corresponding exchange rates as its values. These rates must all
              be based on the same currency, whatever it is. This dictionary must
              also include a rate for the base currencies of the companies we are
              updating rates from, otherwise this will result in an error
              asking the user to choose another provider.

        :return: True if the rates of all the records in self were updated
                 successfully, False if at least one wasn't.
        '''
        active_currencies = self.env['res.currency'].search([])
        rslt = True
        for (currency_provider, companies) in self._group_by_provider().items():
            parse_function = getattr(companies, '_parse_' + currency_provider + '_data')
            try:
                parse_results = parse_function(active_currencies)
                companies._generate_currency_rates(parse_results)
            except Exception:
                rslt = False
                _logger.exception('Unable to connect to the online exchange rate platform %s. The web service may be temporary down.', currency_provider)
        return rslt

    def _group_by_provider(self):
        """ Returns a dictionnary grouping the companies in self by currency
        rate provider. Companies with no provider defined will be ignored."""
        rslt = {}
        for company in self:
            if not company.currency_provider:
                continue

            if rslt.get(company.currency_provider):
                rslt[company.currency_provider] += company
            else:
                rslt[company.currency_provider] = company
        return rslt

    def _generate_currency_rates(self, parsed_data):
        """ Generate the currency rate entries for each of the companies, using the
        result of a parsing function, given as parameter, to get the rates data.

        This function ensures the currency rates of each company are computed,
        based on parsed_data, so that the currency of this company receives rate=1.
        This is done so because a lot of users find it convenient to have the
        exchange rate of their main currency equal to one in datech.
        """
        Currency = self.env['res.currency']
        CurrencyRate = self.env['res.currency.rate']

        for company in self:
            rate_info = parsed_data.get(company.currency_id.name, None)

            if not rate_info:
                raise UserError(_("Your main currency (%s) is not supported by this exchange rate provider. Please choose another one.", company.currency_id.name))

            base_currency_rate = rate_info[0]

            for currency, (rate, date_rate) in parsed_data.items():
                rate_value = rate / base_currency_rate

                currency_object = Currency.search([('name', '=', currency)])
                if currency_object:  # if rate provider base currency is not active, it will be present in parsed_data
                    already_existing_rate = CurrencyRate.search([('currency_id', '=', currency_object.id), ('name', '=', date_rate), ('company_id', '=', company.id)])
                    if already_existing_rate:
                        already_existing_rate.rate = rate_value
                    else:
                        CurrencyRate.create({'currency_id': currency_object.id, 'rate': rate_value, 'name': date_rate, 'company_id': company.id})



    def _parse_bsh_data(self, available_currencies):
        ''' This method is used to update the currencies by using the Central Bank of Egypt service provider.
            Exchange rates are expressed as 1 unit of the foreign currency converted into EGP
        '''

        headers = {
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.bankofalbania.org/Tregjet/Kursi_zyrtar_i_kembimit/'
        }

        fetched_data = requests.get(bsh_URL, headers=headers, timeout=30)
        fetched_data.raise_for_status()


        htmlelem = etree.fromstring(fetched_data.content, etree.HTMLParser(encoding='utf-8'))
        rates_entries = htmlelem.xpath("//table[1]//tr[position() > 1]")
        date_elem = htmlelem.xpath("//div[@class='mb-2']/span/b")[0]
        date_rate = datetime.datetime.strptime(date_elem.text, '%d.%m.%Y').date()
        available_currency_names = set(available_currencies.mapped('name'))
        print(available_currency_names,"available_currency_names")
        rslt = {}
        for rate_entry in rates_entries:
            currency_code = MAP_CURRENCIES.get(rate_entry[0].text)
            rate = float(rate_entry[2].text)
            if currency_code in available_currency_names:
                rslt[currency_code] = (1.0/rate, date_rate)
                print(rslt[currency_code])


        if 'ALL' in available_currency_names:
            rslt['ALL'] = (1.0, date_rate)
        return rslt


    @api.model
    def run_update_currency(self):
        """ This method is called from a cron job to update currency rates.
        """
        records = self.search([('currency_next_execution_date', '<=', fields.Date.today())])
        if records:
            to_update = self.env['res.company']
            for record in records:
                if record.currency_interval_unit == 'daily':
                    next_update = relativedelta(days=+1)
                elif record.currency_interval_unit == 'weekly':
                    next_update = relativedelta(weeks=+1)
                elif record.currency_interval_unit == 'monthly':
                    next_update = relativedelta(months=+1)
                else:
                    record.currency_next_execution_date = False
                    continue
                record.currency_next_execution_date = datetime.date.today() + next_update
                to_update += record
            to_update.update_currency_rates()


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    currency_interval_unit = fields.Selection(related="company_id.currency_interval_unit", readonly=False)
    currency_provider = fields.Selection(related="company_id.currency_provider", readonly=False)
    currency_next_execution_date = fields.Date(related="company_id.currency_next_execution_date", readonly=False)

    @api.onchange('currency_interval_unit')
    def onchange_currency_interval_unit(self):
        #as the onchange is called upon each opening of the settings, we avoid overwriting
        #the next execution date if it has been already set
        if self.company_id.currency_next_execution_date:
            return
        if self.currency_interval_unit == 'daily':
            next_update = relativedelta(days=+1)
        elif self.currency_interval_unit == 'weekly':
            next_update = relativedelta(weeks=+1)
        elif self.currency_interval_unit == 'monthly':
            next_update = relativedelta(months=+1)
        else:
            self.currency_next_execution_date = False
            return
        self.currency_next_execution_date = datetime.date.today() + next_update

    def update_currency_rates_manually(self):
        self.ensure_one()

        if not (self.company_id.update_currency_rates()):
            raise UserError(_('Unable to connect to the online exchange rate platform. The web service may be temporary down. Please try again in a moment.'))
