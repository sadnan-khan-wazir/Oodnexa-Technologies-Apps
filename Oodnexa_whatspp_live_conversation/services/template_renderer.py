"""
Template Rendering Engine
=========================
Safe variable substitution for WhatsApp message templates.
Uses a restricted placeholder syntax: {{variable_name}}
No arbitrary code execution—only simple field lookups.
"""
import json
import logging
import re
from odoo import api, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Only allow alphanumeric + underscore in variable names
_VAR_PATTERN = re.compile(r'\{\{(\w+)\}\}')

# Built-in variable resolvers (key → lambda(record, partner, company))
_BUILTIN_VARIABLES = {
    'customer_name': lambda rec, partner, company: partner.name if partner else '',
    'customer_first_name': lambda rec, partner, company: (partner.name or '').split()[0] if partner else '',
    'customer_email': lambda rec, partner, company: partner.email if partner else '',
    'customer_phone': lambda rec, partner, company: (getattr(partner, 'phone', False) or getattr(partner, 'mobile', False) or '') if partner else '',
    'company_name': lambda rec, partner, company: company.name if company else '',
    'company_phone': lambda rec, partner, company: company.phone or '' if company else '',
    'company_email': lambda rec, partner, company: company.email or '' if company else '',
    'company_website': lambda rec, partner, company: company.website or '' if company else '',
    'salesperson_name': lambda rec, partner, company: (
        rec.user_id.name if hasattr(rec, 'user_id') and rec.user_id else ''
    ),
    'record_name': lambda rec, partner, company: rec.display_name if rec else '',
    'portal_url': lambda rec, partner, company: (
        (rec.get_portal_url() if hasattr(rec, 'get_portal_url') else '') or
        (partner._get_signup_url() if hasattr(partner, '_get_signup_url') else '') or
        (f"{(company.website or '').rstrip('/')}/my" if company and company.website else '')
    ),
}


class WhatsappTemplateRendererService(models.AbstractModel):
    _name = 'whatsapp.automation.template.renderer'
    _description = 'WhatsApp Template Renderer'

    def render(self, template, record=None, partner=None, extra_vars=None):
        """
        Render a whatsapp.automation.template for a given record.

        :param template: whatsapp.automation.template record
        :param record: source record (e.g., sale.order, crm.lead)
        :param partner: res.partner (recipient)
        :param extra_vars: dict of additional variables
        :return: dict with rendered header, body, footer, and variable_values
        """
        company = self.env.company
        variables = self._collect_variables(template, record, partner, company, extra_vars)

        rendered_header = self._substitute(template.header_text or '', variables)
        rendered_body = self._substitute(template.body_text or '', variables)
        rendered_footer = self._substitute(template.footer_text or '', variables)

        return {
            'header': rendered_header,
            'body': rendered_body,
            'footer': rendered_footer,
            'variable_values': variables,
        }

    def render_body_only(self, template, record=None, partner=None, extra_vars=None):
        """Render only the body text — convenience method."""
        result = self.render(template, record=record, partner=partner, extra_vars=extra_vars)
        return result['body']

    def preview(self, template, sample_record=None, sample_partner=None):
        """Generate a preview render with sample/fallback values."""
        return self.render(
            template,
            record=sample_record,
            partner=sample_partner or self.env.user.partner_id,
        )

    def get_available_variables(self, template, record=None):
        """Return list of available variable names for a template."""
        detected = set(_VAR_PATTERN.findall(template.body_text or ''))
        detected |= set(_VAR_PATTERN.findall(template.header_text or ''))
        detected |= set(_VAR_PATTERN.findall(template.footer_text or ''))

        result = []
        for var in sorted(detected):
            source = 'builtin' if var in _BUILTIN_VARIABLES else 'schema'
            result.append({'name': var, 'source': source})
        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _collect_variables(self, template, record, partner, company, extra_vars):
        """Build variable dict from all sources."""
        variables = {}

        # 1) Built-in variables
        for key, resolver in _BUILTIN_VARIABLES.items():
            try:
                variables[key] = resolver(record, partner, company) or ''
            except Exception:
                variables[key] = ''

        # 2) Schema-defined variables (from template.variable_schema_json)
        if template.variable_schema_json:
            try:
                schema = json.loads(template.variable_schema_json)
                for item in schema:
                    var_name = item.get('name', '')
                    field_path = item.get('field', '')
                    default = item.get('default', '')
                    if var_name and field_path and record:
                        variables[var_name] = self._resolve_field_path(record, field_path, default)
                    elif var_name:
                        variables[var_name] = default
            except (json.JSONDecodeError, TypeError) as e:
                _logger.warning('Failed parsing variable schema: %s', e)

        # 3) Record-specific auto-detect
        if record:
            variables.update(self._auto_detect_fields(record))

        # 4) Extra variables override
        if extra_vars and isinstance(extra_vars, dict):
            variables.update(extra_vars)

        return variables

    def _substitute(self, text, variables):
        """Replace {{var}} placeholders with values. Unknown vars get empty string."""
        if not text:
            return ''

        def _replacer(match):
            var_name = match.group(1)
            value = variables.get(var_name, '')
            # Safety: strip any HTML-like content from values
            if isinstance(value, str):
                value = value.replace('<', '&lt;').replace('>', '&gt;')
            return str(value)

        return _VAR_PATTERN.sub(_replacer, text)

    def _resolve_field_path(self, record, field_path, default=''):
        """Safely resolve a dotted field path on a record."""
        try:
            obj = record
            for part in field_path.split('.'):
                if not part or not hasattr(obj, part):
                    return default
                obj = getattr(obj, part)
                if obj is None or obj is False:
                    return default
            return str(obj) if obj else default
        except Exception:
            return default

    def _auto_detect_fields(self, record):
        """Auto-detect common business fields from the record."""
        auto = {}
        field_map = {
            'order_number': ['name', 'display_name'],
            'order_total': ['total', 'amount_total'],
            'order_date': ['order_date', 'date_order', 'create_date'],
            'amount_residual': ['amount_residual'],
            'due_date': ['date_due', 'invoice_date_due', 'date_deadline'],
            'stage_name': ['stage_id.name'],
            'tracking_link': ['carrier_tracking_url'],
        }
        for var_name, candidates in field_map.items():
            for field in candidates:
                val = self._resolve_field_path(record, field)
                if val:
                    auto[var_name] = val
                    break
        return auto
