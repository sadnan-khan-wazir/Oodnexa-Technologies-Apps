from odoo import api, fields, models, _


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # Global toggle
    wa_automation_enabled = fields.Boolean(
        string='Enable WhatsApp Automation',
        config_parameter='Oodnexa_whatspp_live_conversation.enabled',
        default=True,
    )
    # Default provider
    wa_default_provider_id = fields.Many2one(
        'whatsapp.automation.provider',
        string='Default WhatsApp Provider',
        config_parameter='Oodnexa_whatspp_live_conversation.default_provider_id',
    )
    # Retry
    wa_default_retry_attempts = fields.Integer(
        string='Default Retry Attempts',
        config_parameter='Oodnexa_whatspp_live_conversation.retry_attempts',
        default=3,
    )
    wa_retry_delay_minutes = fields.Integer(
        string='Retry Delay (minutes)',
        config_parameter='Oodnexa_whatspp_live_conversation.retry_delay_minutes',
        default=5,
    )
    # Queue
    wa_queue_batch_size = fields.Integer(
        string='Queue Batch Size',
        config_parameter='Oodnexa_whatspp_live_conversation.queue_batch_size',
        default=50,
    )
    # Feature toggles
    wa_enable_reminders = fields.Boolean(
        string='Enable Reminders',
        config_parameter='Oodnexa_whatspp_live_conversation.enable_reminders',
        default=True,
    )
    wa_enable_crm_automation = fields.Boolean(
        string='Enable CRM Automation',
        config_parameter='Oodnexa_whatspp_live_conversation.enable_crm_automation',
        default=True,
    )
    wa_enable_sales_updates = fields.Boolean(
        string='Enable Sales/Order Updates',
        config_parameter='Oodnexa_whatspp_live_conversation.enable_sales_updates',
        default=True,
    )
    wa_enable_invoice_reminders = fields.Boolean(
        string='Enable Invoice Reminders',
        config_parameter='Oodnexa_whatspp_live_conversation.enable_invoice_reminders',
        default=True,
    )
    wa_require_approved_templates = fields.Boolean(
        string='Require Approved Templates Only',
        config_parameter='Oodnexa_whatspp_live_conversation.require_approved_templates',
        default=False,
    )
    # Anti-spam
    wa_duplicate_prevention_window = fields.Integer(
        string='Duplicate Prevention (hours)',
        config_parameter='Oodnexa_whatspp_live_conversation.duplicate_window_hours',
        default=24,
        help='Minimum hours between duplicate messages to the same phone for the same rule.',
    )
    # Log retention
    wa_log_retention_days = fields.Integer(
        string='Log Retention (days)',
        config_parameter='Oodnexa_whatspp_live_conversation.log_retention_days',
        default=90,
    )
    # Business hours (placeholder)
    wa_send_window_start = fields.Float(
        string='Send Window Start (hour)',
        config_parameter='Oodnexa_whatspp_live_conversation.send_window_start',
        default=8.0,
    )
    wa_send_window_end = fields.Float(
        string='Send Window End (hour)',
        config_parameter='Oodnexa_whatspp_live_conversation.send_window_end',
        default=20.0,
    )
