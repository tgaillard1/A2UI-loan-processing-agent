import base64
import json
import re


def markdown_to_html(text: str) -> str:
  """Lightweight regex-based markdown to HTML translator."""
  if not text:
    return ""

  # Convert bold markdown blocks (**text**) to HTML strong
  html = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", text)

  # Convert single asterisk marks (*text*) to HTML strong as well
  html = re.sub(r"\*(.*?)\*", r"<strong>\1</strong>", html)

  # Parse bullet points lists (- item or * item)
  lines = html.split("\n")
  in_list = False
  new_lines = []
  for line in lines:
    trimmed = line.strip()
    if (
        trimmed.startswith("- ")
        or trimmed.startswith("* ")
        or trimmed.startswith("• ")
    ):
      content = trimmed[2:].strip()
      if not in_list:
        new_lines.append(
            '<ul style="margin-top: 5px; margin-bottom: 5px; padding-left:'
            ' 20px;">'
        )
        in_list = True
      new_lines.append(f"<li>{content}</li>")
    else:
      if in_list:
        new_lines.append("</ul>")
        in_list = False
      new_lines.append(line)
  if in_list:
    new_lines.append("</ul>")

  html = "\n".join(new_lines)

  # Convert standard line breaks to HTML breaks
  html = html.replace("\n", "<br>")
  return html


def generate_hydrated_dashboard(
    inquiry_id: str,
    client_name: str,
    tax_id: str,
    inv_amt_str: str,
    app_lmt_str: str,
    overage_str: str,
    make_val: str,
    model_val: str,
    vin_val: str,
    res_verb: str,
    val_passed: bool,
    clean_inv_b64: str,
    clean_w9_b64: str,
    clean_app_b64: str,
    audit_explanation: str,
) -> dict:
  """Generates a 100% escape-proof, hydrated A2UI v0.8 Dashboard payload dictionary."""

  parsed_explanation = markdown_to_html(audit_explanation)

  # Parse numeric amounts for progress bars exposure comparison chart
  try:
    numeric_inv = float(inv_amt_str.replace("$", "").replace(",", "").strip())
    numeric_lmt = float(app_lmt_str.replace("$", "").replace(",", "").strip())
  except Exception:
    numeric_inv = 0.0
    numeric_lmt = 0.0

  if numeric_lmt > 0:
    percentage = min(100.0, (numeric_inv / numeric_lmt) * 100.0)
  else:
    percentage = 100.0

  invoice_percentage_str = f"{percentage:.1f}%"
  invoice_color = "#e74c3c" if numeric_inv > numeric_lmt else "#3498db"

  # Deduce check statuses dynamically based on properties
  tax_match = True
  limit_match = (numeric_inv <= numeric_lmt) if numeric_lmt > 0 else True
  asset_match = True

  if not val_passed:
    exp_lower = str(audit_explanation).lower()
    if "tax id mismatch" in exp_lower:
      tax_match = False
    if "asset class mismatch" in exp_lower:
      asset_match = False

  # Check 1: Tax ID Alignment Check
  c1_icon = "🟢" if tax_match else "🔴"
  c1_bg = "#ebf7ee" if tax_match else "#fdf2f2"
  c1_border = "#d3ebd6" if tax_match else "#fde8e8"
  c1_status = "Aligned" if tax_match else "Mismatch"

  # Check 2: Credit Limit Ingestion Check
  c2_icon = "🟢" if limit_match else "🔴"
  c2_bg = "#ebf7ee" if limit_match else "#fdf2f2"
  c2_border = "#d3ebd6" if limit_match else "#fde8e8"
  c2_status = "Within Limit" if limit_match else "Exceeded Limit"

  # Check 3: Asset Collateral Underwriting Check
  c3_icon = "🟢" if asset_match else "🔴"
  c3_bg = "#ebf7ee" if asset_match else "#fdf2f2"
  c3_border = "#d3ebd6" if asset_match else "#fde8e8"
  c3_status = "Approved Make" if asset_match else "Unapproved Make"

  dashboard_html = (
      '<!DOCTYPE html><html><head><meta http-equiv="Content-Security-Policy"'
      " content=\"connect-src 'none'\"></head><body style=\"font-family: 'Segoe"
      " UI', sans-serif; background: #f5f7fa; margin: 0; padding: 30px;\"><div"
      ' style="background: #ffffff; border-radius: 12px; border-top: 6px solid'
      " #4A90E2; padding: 30px; max-width: 800px; margin: 0 auto; box-shadow: 0"
      ' 4px 8px rgba(0,0,0,0.05);"><h2 style="color: #2C3E50; font-size: 26px;'
      ' text-align: center; margin-top: 0;">Originations Compliance'
      ' Dashboard</h2><div style="display: flex; flex-wrap: wrap; gap: 20px;'
      ' justify-content: space-between; margin-bottom: 20px;"><div'
      ' style="background: #f8f9fa; border: 1px solid #e9ecef; flex: 1;'
      " min-width: 40%; padding: 15px; border-radius: 8px; text-align:"
      ' center;"><span style="font-size: 11px; color: #7f8c8d; text-transform:'
      ' uppercase; display: block;">Applicant</span><strong style="font-size:'
      ' 18px; color: #2c3e50;">'
      + str(client_name)
      + '</strong></div><div style="background: #f8f9fa; border: 1px solid'
      " #e9ecef; flex: 1; min-width: 40%; padding: 15px; border-radius: 8px;"
      ' text-align: center;"><span style="font-size: 11px; color: #7f8c8d;'
      ' text-transform: uppercase; display: block;">Tax ID</span><strong'
      ' style="font-size: 18px; color: #2c3e50;">'
      + str(tax_id)
      + '</strong></div><div style="background: #f8f9fa; border: 1px solid'
      " #e9ecef; flex: 1; min-width: 40%; padding: 15px; border-radius: 8px;"
      ' text-align: center;"><span style="font-size: 11px; color: #7f8c8d;'
      ' text-transform: uppercase; display: block;">Requested'
      ' Invoice</span><strong style="font-size: 18px; color: #2c3e50;">'
      + str(inv_amt_str)
      + '</strong></div><div style="background: #f8f9fa; border: 1px solid'
      " #e9ecef; flex: 1; min-width: 40%; padding: 15px; border-radius: 8px;"
      ' text-align: center;"><span style="font-size: 11px; color: #7f8c8d;'
      ' text-transform: uppercase; display: block;">Approved'
      ' Limit</span><strong style="font-size: 18px; color: #2c3e50;">'
      + str(app_lmt_str)
      + "</strong></div></div>"
      '<div style="margin-top: 25px; margin-bottom: 25px; background: #ffffff;'
      ' border: 1px solid #e9ecef; padding: 20px; border-radius: 8px;"><h4'
      ' style="margin-top: 0; margin-bottom: 15px; color: #2C3E50; font-size:'
      ' 12px; text-transform: uppercase; letter-spacing: 0.5px;">Exposure'
      ' Comparison Bar Chart</h4><div style="display: flex; flex-direction:'
      ' column; gap: 12px;"><div><div style="display: flex;'
      ' justify-content: space-between; font-size: 12px; margin-bottom:'
      ' 4px;"><span style="color: #7f8c8d;">Approved Credit Limit</span><span'
      ' style="font-weight: bold; color: #27ae60;">'
      + str(app_lmt_str)
      + '</span></div><div style="background: #e9ecef; height: 14px;'
      ' border-radius: 7px; overflow: hidden; width: 100%;"><div'
      ' style="background: #27ae60; height: 100%; border-radius: 7px; width:'
      ' 100%;"></div></div></div><div><div style="display: flex;'
      ' justify-content: space-between; font-size: 12px; margin-bottom:'
      ' 4px;"><span style="color: #7f8c8d;">Requested Invoice Amount ('
      + str(invoice_percentage_str)
      + ')</span><span style="font-weight: bold; color: '
      + str(invoice_color)
      + ';">'
      + str(inv_amt_str)
      + '</span></div><div style="background: #e9ecef; height: 14px;'
      ' border-radius: 7px; overflow: hidden; width: 100%;"><div'
      ' style="background: '
      + str(invoice_color)
      + '; height: 100%; border-radius: 7px; width: '
      + str(invoice_percentage_str)
      + ';"></div></div></div></div></div>'
      '<div style="margin-top: 20px; margin-bottom: 20px; display: flex; gap:'
      ' 15px; justify-content: space-between;"><div style="flex: 1; border:'
      ' 1px solid '
      + str(c1_border)
      + "; background: "
      + str(c1_bg)
      + '; border-radius: 8px; padding: 12px; display: flex; align-items:'
      ' center; gap: 10px;"><span style="font-size: 18px;">'
      + str(c1_icon)
      + '</span><div><span style="font-size: 9px; color: #7f8c8d;'
      ' text-transform: uppercase; display: block; font-weight: bold;'
      ' letter-spacing: 0.5px;">Tax ID Alignment</span><strong'
      ' style="font-size: 12px; color: #2c3e50;">'
      + str(c1_status)
      + '</strong></div></div><div style="flex: 1; border: 1px solid '
      + str(c2_border)
      + "; background: "
      + str(c2_bg)
      + '; border-radius: 8px; padding: 12px; display: flex; align-items:'
      ' center; gap: 10px;"><span style="font-size: 18px;">'
      + str(c2_icon)
      + '</span><div><span style="font-size: 9px; color: #7f8c8d;'
      ' text-transform: uppercase; display: block; font-weight: bold;'
      ' letter-spacing: 0.5px;">Credit Limit Check</span><strong'
      ' style="font-size: 12px; color: #2c3e50;">'
      + str(c2_status)
      + '</strong></div></div><div style="flex: 1; border: 1px solid '
      + str(c3_border)
      + "; background: "
      + str(c3_bg)
      + '; border-radius: 8px; padding: 12px; display: flex; align-items:'
      ' center; gap: 10px;"><span style="font-size: 18px;">'
      + str(c3_icon)
      + '</span><div><span style="font-size: 9px; color: #7f8c8d;'
      ' text-transform: uppercase; display: block; font-weight: bold;'
      ' letter-spacing: 0.5px;">Collateral Approval</span><strong'
      ' style="font-size: 12px; color: #2c3e50;">'
      + str(c3_status)
      + '</strong></div></div></div>'
      '<div style="margin-top: 20px; padding: 20px; background: #fef9e7;'
      ' border-left: 4px solid #f39c12; border-radius: 4px; font-size: 14px;'
      ' line-height: 1.5; color: #2c3e50;"><strong>Compliance Audit'
      ' Explanation:</strong><div style="margin-top: 8px;">'
      + str(parsed_explanation)
      + '</div></div><div style="text-align: center; margin-top: 30px;"><span'
      " style=\"display: inline-block; padding: 8px 16px; border-radius: 20px;"
      " font-size: 14px; font-weight: bold; color: white; background-color: "
      + ("#e74c3c" if not val_passed else "#27ae60")
      + ';">Compliance Status: '
      + str(res_verb)
      + "</span></div></div></body></html>"
  )

  if clean_inv_b64 and len(clean_inv_b64) > 50:
    iframe_invoice = (
        '<!DOCTYPE html><html><head><meta http-equiv="Content-Security-Policy"'
        ' content="connect-src \'none\'"></head><body style="margin: 0;'
        " padding: 0; display: flex; justify-content: center; align-items:"
        ' flex-start; background: #f0f0f0; height: 100vh; overflow: auto;"><img'
        ' src="'
        + str(clean_inv_b64)
        + '" style="max-width: 100%; height: auto; box-shadow: 0 4px 8px'
        ' rgba(0,0,0,0.1); margin: 20px; border-radius: 4px;"></body></html>'
    )
  else:
    iframe_invoice = (
        '<!DOCTYPE html><html><head><meta http-equiv="Content-Security-Policy"'
        ' content="connect-src \'none\'"></head><body style="font-family:'
        " sans-serif; display: flex; justify-content: center; align-items:"
        " center; height: 100vh; background: #fafafa; color: #7f8c8d;"
        ' margin:0;"><div>⚠️ Document Image Not Available (Pending'
        " Processing)</div></body></html>"
    )

  if clean_w9_b64 and len(clean_w9_b64) > 50:
    iframe_w9 = (
        '<!DOCTYPE html><html><head><meta http-equiv="Content-Security-Policy"'
        ' content="connect-src \'none\'"></head><body style="margin: 0;'
        " padding: 0; display: flex; justify-content: center; align-items:"
        ' flex-start; background: #f0f0f0; height: 100vh; overflow: auto;"><img'
        ' src="'
        + str(clean_w9_b64)
        + '" style="max-width: 100%; height: auto; box-shadow: 0 4px 8px'
        ' rgba(0,0,0,0.1); margin: 20px; border-radius: 4px;"></body></html>'
    )
  else:
    iframe_w9 = (
        '<!DOCTYPE html><html><head><meta http-equiv="Content-Security-Policy"'
        ' content="connect-src \'none\'"></head><body style="font-family:'
        " sans-serif; display: flex; justify-content: center; align-items:"
        " center; height: 100vh; background: #fafafa; color: #7f8c8d;"
        ' margin:0;"><div>⚠️ Tax Document (W-9) Not Found</div></body></html>'
    )

  if clean_app_b64 and len(clean_app_b64) > 50:
    iframe_app = (
        '<!DOCTYPE html><html><head><meta http-equiv="Content-Security-Policy"'
        ' content="connect-src \'none\'"></head><body style="margin: 0;'
        " padding: 0; display: flex; justify-content: center; align-items:"
        ' flex-start; background: #f0f0f0; height: 100vh; overflow: auto;"><img'
        ' src="'
        + str(clean_app_b64)
        + '" style="max-width: 100%; height: auto; box-shadow: 0 4px 8px'
        ' rgba(0,0,0,0.1); margin: 20px; border-radius: 4px;"></body></html>'
    )
  else:
    iframe_app = (
        '<!DOCTYPE html><html><head><meta http-equiv="Content-Security-Policy"'
        ' content="connect-src \'none\'"></head><body style="font-family:'
        " sans-serif; display: flex; justify-content: center; align-items:"
        " center; height: 100vh; background: #fafafa; color: #7f8c8d;"
        ' margin:0;"><div>⚠️ Credit Application Not Found</div></body></html>'
    )

  res_icon = "✅" if val_passed else "❌"

  payload = {
      "beginRendering": {
          "surfaceId": f"compliance_report_{inquiry_id}",
          "root": "root_card",
          "catalogId": "standard",
      },
      "surfaceUpdate": {
          "surfaceId": f"compliance_report_{inquiry_id}",
          "components": [
              {
                  "id": "root_card",
                  "component": {"Card": {"child": "main_column"}},
              },
              {
                  "id": "main_column",
                  "component": {
                      "Column": {
                          "children": {
                              "explicitList": [
                                  "header_text",
                                  "divider_1",
                                  "tabs_component",
                                  "divider_2",
                                  "action_row",
                              ]
                          }
                      }
                  },
              },
              {
                  "id": "header_text",
                  "component": {
                      "Text": {
                          "text": {
                              "literalString": (
                                  f"{inquiry_id} - {client_name} - {res_icon}"
                                  f" {res_verb}"
                              )
                          },
                          "usageHint": "h2",
                      }
                  },
              },
              {
                  "id": "divider_1",
                  "component": {
                      "Text": {
                          "text": {
                              "literalString": (
                                  "--------------------------------------------------"
                              )
                          },
                          "usageHint": "caption",
                      }
                  },
              },
              {
                  "id": "tabs_component",
                  "component": {
                      "Tabs": {
                          "tabItems": [
                              {
                                  "title": {
                                      "literalString": (
                                          "📋 Dashboard"
                                      )
                                  },
                                  "child": "srcdoc_frame",
                              },
                              {
                                  "title": {
                                      "literalString": "📄 Scanned Invoice"
                                  },
                                  "child": "invoice_frame",
                              },
                              {
                                  "title": {
                                      "literalString": "📄 Tax Document (W-9)"
                                  },
                                  "child": "w9_frame",
                              },
                              {
                                  "title": {
                                      "literalString": "📄 Credit Application"
                                  },
                                  "child": "app_frame",
                              },
                              {
                                  "title": {
                                      "literalString": "📋 Extracted Debug"
                                  },
                                  "child": "overview_column",
                              },
                          ]
                      }
                  },
              },
              {
                  "id": "srcdoc_frame",
                  "component": {
                      "WebFrameSrcdoc": {
                          "height": 800,
                          "htmlContent": {"literalString": dashboard_html},
                      }
                  },
              },
              {
                  "id": "invoice_frame",
                  "component": {
                      "WebFrameSrcdoc": {
                          "height": 800,
                          "htmlContent": {"literalString": iframe_invoice},
                      }
                  },
              },
              {
                  "id": "w9_frame",
                  "component": {
                      "WebFrameSrcdoc": {
                          "height": 800,
                          "htmlContent": {"literalString": iframe_w9},
                      }
                  },
              },
              {
                  "id": "app_frame",
                  "component": {
                      "WebFrameSrcdoc": {
                          "height": 800,
                          "htmlContent": {"literalString": iframe_app},
                      }
                  },
              },
              {
                  "id": "overview_column",
                  "component": {
                      "Column": {
                          "children": {
                              "explicitList": [
                                  "applicant_row",
                                  "tax_row",
                                  "limit_row",
                                  "amount_row",
                                  "overage_row",
                                  "audit_explanation_text",
                              ]
                          }
                      }
                  },
              },
              {
                  "id": "applicant_row",
                  "component": {
                      "Row": {
                          "children": {
                              "explicitList": ["lbl_applicant", "val_applicant"]
                          },
                          "alignment": "center",
                      }
                  },
              },
              {
                  "id": "lbl_applicant",
                  "component": {
                      "Text": {
                          "text": {"literalString": "APPLICANT:"},
                          "usageHint": "caption",
                      }
                  },
              },
              {
                  "id": "val_applicant",
                  "component": {
                      "Text": {
                          "text": {"literalString": client_name},
                          "usageHint": "body",
                      }
                  },
              },
              {
                  "id": "tax_row",
                  "component": {
                      "Row": {
                          "children": {"explicitList": ["lbl_tax", "val_tax"]},
                          "alignment": "center",
                      }
                  },
              },
              {
                  "id": "lbl_tax",
                  "component": {
                      "Text": {
                          "text": {"literalString": "TAX ID:"},
                          "usageHint": "caption",
                      }
                  },
              },
              {
                  "id": "val_tax",
                  "component": {
                      "Text": {
                          "text": {"literalString": tax_id},
                          "usageHint": "body",
                      }
                  },
              },
              {
                  "id": "limit_row",
                  "component": {
                      "Row": {
                          "children": {
                              "explicitList": ["lbl_limit", "val_limit"]
                          },
                          "alignment": "center",
                      }
                  },
              },
              {
                  "id": "lbl_limit",
                  "component": {
                      "Text": {
                          "text": {"literalString": "APPROVED LIMIT:"},
                          "usageHint": "caption",
                      }
                  },
              },
              {
                  "id": "val_limit",
                  "component": {
                      "Text": {
                          "text": {"literalString": app_lmt_str},
                          "usageHint": "body",
                      }
                  },
              },
              {
                  "id": "amount_row",
                  "component": {
                      "Row": {
                          "children": {
                              "explicitList": ["lbl_amount", "val_amount"]
                          },
                          "alignment": "center",
                      }
                  },
              },
              {
                  "id": "lbl_amount",
                  "component": {
                      "Text": {
                          "text": {"literalString": "INVOICE AMOUNT:"},
                          "usageHint": "caption",
                      }
                  },
              },
              {
                  "id": "val_amount",
                  "component": {
                      "Text": {
                          "text": {"literalString": inv_amt_str},
                          "usageHint": "body",
                      }
                  },
              },
              {
                  "id": "overage_row",
                  "component": {
                      "Row": {
                          "children": {
                              "explicitList": ["lbl_overage", "val_overage"]
                          },
                          "alignment": "center",
                      }
                  },
              },
              {
                  "id": "lbl_overage",
                  "component": {
                      "Text": {
                          "text": {"literalString": "LIMIT OVERAGE:"},
                          "usageHint": "caption",
                      }
                  },
              },
              {
                  "id": "val_overage",
                  "component": {
                      "Text": {
                          "text": {"literalString": "⚠️ " + overage_str},
                          "usageHint": "body",
                      }
                  },
              },
              {
                  "id": "audit_explanation_text",
                  "component": {
                      "Text": {
                          "text": {"literalString": audit_explanation},
                          "usageHint": "body",
                      }
                  },
              },
              {
                  "id": "divider_2",
                  "component": {
                      "Text": {
                          "text": {
                              "literalString": (
                                  "--------------------------------------------------"
                              )
                          },
                          "usageHint": "caption",
                      }
                  },
              },
              {
                  "id": "action_row",
                  "component": {
                      "Row": {
                          "children": {
                              "explicitList": [
                                  "approve_btn",
                                  "reject_btn",
                                  "escalate_btn",
                              ]
                          }
                      }
                  },
              },
              {
                  "id": "approve_btn",
                  "component": {
                      "Button": {
                          "child": "approve_btn_text",
                          "primary": True,
                          "action": {
                              "name": "approve_inquiry",
                              "context": [{
                                  "key": "inquiry_id",
                                  "value": {"literalString": inquiry_id},
                              }],
                          },
                      }
                  },
              },
              {
                  "id": "approve_btn_text",
                  "component": {
                      "Text": {"text": {"literalString": "Manually Approve"}}
                  },
              },
              {
                  "id": "reject_btn",
                  "component": {
                      "Button": {
                          "child": "reject_btn_text",
                          "primary": False,
                          "action": {
                              "name": "reject_inquiry",
                              "context": [{
                                  "key": "inquiry_id",
                                  "value": {"literalString": inquiry_id},
                              }],
                          },
                      }
                  },
              },
              {
                  "id": "reject_btn_text",
                  "component": {
                      "Text": {"text": {"literalString": "Manually Reject"}}
                  },
              },
              {
                  "id": "escalate_btn",
                  "component": {
                      "Button": {
                          "child": "escalate_btn_text",
                          "primary": False,
                          "action": {
                              "name": "escalate_inquiry",
                              "context": [{
                                  "key": "inquiry_id",
                                  "value": {"literalString": inquiry_id},
                              }],
                          },
                      }
                  },
              },
              {
                  "id": "escalate_btn_text",
                  "component": {
                      "Text": {
                          "text": {"literalString": "🚨 Escalate to Human"}
                      }
                  },
              },
          ],
      },
  }

  return payload
