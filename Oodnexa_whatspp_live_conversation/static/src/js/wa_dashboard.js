/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

/**
 * WhatsApp Automation Dashboard Client Action
 * Shows key metrics and quick actions for WA automation.
 */
class WaDashboard extends Component {
      static template = "Oodnexa_whatspp_live_conversation.Dashboard";
      static props = ["*"];

      setup() {
            this.orm = useService("orm");
            this.action = useService("action");
            this.state = useState({
                  totalMessages: 0,
                  sentCount: 0,
                  deliveredCount: 0,
                  failedCount: 0,
                  queuedCount: 0,
                  activeRules: 0,
                  activeProviders: 0,
                  loading: true,
            });

            onWillStart(async () => {
                  await this.loadDashboardData();
            });
      }

      async loadDashboardData() {
            try {
                  const [messages, queuedMsgs, rules, providers] = await Promise.all([
                        this.orm.searchCount("whatsapp.automation.message", []),
                        this.orm.searchCount("whatsapp.automation.queue", [["state", "in", ["pending", "scheduled"]]]),
                        this.orm.searchCount("whatsapp.automation.rule", [["state", "=", "active"]]),
                        this.orm.searchCount("whatsapp.automation.provider", [["status", "=", "active"]]),
                  ]);

                  const [sent, delivered, failed] = await Promise.all([
                        this.orm.searchCount("whatsapp.automation.message", [["state", "=", "sent"]]),
                        this.orm.searchCount("whatsapp.automation.message", [["state", "=", "delivered"]]),
                        this.orm.searchCount("whatsapp.automation.message", [["state", "=", "failed"]]),
                  ]);

                  Object.assign(this.state, {
                        totalMessages: messages,
                        sentCount: sent,
                        deliveredCount: delivered,
                        failedCount: failed,
                        queuedCount: queuedMsgs,
                        activeRules: rules,
                        activeProviders: providers,
                        loading: false,
                  });
            } catch {
                  this.state.loading = false;
            }
      }

      openMessages(state) {
            this.action.doAction({
                  type: "ir.actions.act_window",
                  name: "Messages",
                  res_model: "whatsapp.automation.message",
                  view_mode: "list,form",
                  domain: state ? [["state", "=", state]] : [],
                  views: [[false, "list"], [false, "form"]],
            });
      }

      openRules() {
            this.action.doAction({
                  type: "ir.actions.act_window",
                  name: "Active Rules",
                  res_model: "whatsapp.automation.rule",
                  view_mode: "list,form",
                  domain: [["state", "=", "active"]],
                  views: [[false, "list"], [false, "form"]],
            });
      }

      openQueue() {
            this.action.doAction({
                  type: "ir.actions.act_window",
                  name: "Queue",
                  res_model: "whatsapp.automation.queue",
                  view_mode: "list,form",
                  domain: [["state", "in", ["pending", "scheduled"]]],
                  views: [[false, "list"], [false, "form"]],
            });
      }
}

WaDashboard.template = "Oodnexa_whatspp_live_conversation.Dashboard";

registry.category("actions").add("Oodnexa_whatspp_live_conversation.dashboard", WaDashboard);
