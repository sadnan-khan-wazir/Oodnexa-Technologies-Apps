{
    'name': 'whatsapp Live conversation',
    'version': '19.0.1.0.0',
    'category': 'Marketing/WhatsApp',
    'summary': 'WhatsApp Live Chat, 1-Click Quotations, Voice Notes, Media & Official Meta Cloud API for Odoo 19',
    'description': """
whatsapp Live conversation
==========================
Transform customer communication in Odoo 19 with real-time WhatsApp live chat, 
direct quotation generation, voice notes, rich media, and official Meta Cloud API integration.

Key Features:
- Split-screen WhatsApp Live Chat Console
- 1-Click Sales Quotation creation directly from chat conversation
- Live HTML5 Voice Note recorder with waveform playback
- Full media support: Images, videos, PDF documents, and invoices
- 100% Mobile Responsive design for desktop, tablet, and mobile
- Official Meta WhatsApp Cloud API (Graph API v21.0) with zero subscription fees
- Webhook receiver for instant two-way real-time messaging
- Automated customer follow-ups, templates, and dynamic variable engine
    """,
    'author': 'Oodnexa Technologies',
    'website': '',
    'email': 'sadnanhussain2@gmail.com',
    'license': 'OPL-1',
    'depends': [
        'base',
        'web',
        'mail',
        'contacts',
        'crm',
        'sale_management',
        'account',
        'stock',
    ],
    'data': [
        # Security
        'security/security_groups.xml',
        'security/ir.model.access.csv',
        # Data
        'data/category_data.xml',
        'data/template_data.xml',
        'data/cron_data.xml',
        # Views
        'views/wa_category_views.xml',
        'views/wa_provider_views.xml',
        'views/wa_template_views.xml',
        'views/wa_rule_views.xml',
        'views/wa_message_views.xml',
        'views/wa_queue_views.xml',
        'views/wa_log_views.xml',
        'views/wa_campaign_views.xml',
        'views/wa_dashboard_views.xml',
        'views/res_config_settings_views.xml',
        'views/res_partner_views.xml',
        'views/sale_order_views.xml',
        'views/wa_conversation_views.xml',
        # Menu (must be last to reference all actions)
        'views/menu.xml',

        # Wizard
        'wizard/wa_bulk_send_wizard_views.xml',
        'wizard/wa_sale_send_wizard_views.xml',
        'wizard/wa_send_wizard_views.xml',
    ],
    'demo': [
        'demo/demo_data.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'Oodnexa_whatspp_live_conversation/static/src/css/wa_backend.scss',
            'Oodnexa_whatspp_live_conversation/static/src/js/wa_dashboard.js',
            'Oodnexa_whatspp_live_conversation/static/src/js/wa_dashboard.xml',
            'Oodnexa_whatspp_live_conversation/static/src/js/wa_live_chat.js',
            'Oodnexa_whatspp_live_conversation/static/src/js/wa_live_console.js',
            'Oodnexa_whatspp_live_conversation/static/src/js/wa_live_console.xml',
        ],
    },
    'images': [
        'static/description/banner.png',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    'price': 99.00,
    'currency': 'USD',
    'sequence': 200,
}
