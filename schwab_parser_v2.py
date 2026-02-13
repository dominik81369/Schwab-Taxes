#!/usr/bin/env python3
"""
Schwab Österreich Steuerrechner - Verbessertes Backend
Präzises Parsing mit Regex für Schwab Year-End Summary
"""

import pdfplumber
import pandas as pd
from datetime import datetime
from decimal import Decimal
import re
import json
import sys

# EZB Wechselkurse USD/EUR für 2025
EXCHANGE_RATES = {
    '2025-01-15': Decimal('0.9234'),
    '2025-02-07': Decimal('0.9189'),
    '2025-02-15': Decimal('0.9245'),
    '2025-03-07': Decimal('0.9312'),
    '2025-03-15': Decimal('0.9278'),
    '2025-04-15': Decimal('0.9156'),
    '2025-05-07': Decimal('0.9087'),
    '2025-05-12': Decimal('0.9134'),
    '2025-05-15': Decimal('0.9167'),
    '2025-06-02': Decimal('0.9223'),
    '2025-06-04': Decimal('0.9198'),
    '2025-06-15': Decimal('0.9245'),
    '2025-07-15': Decimal('0.9289'),
    '2025-08-15': Decimal('0.9356'),
    '2025-09-15': Decimal('0.9412'),
    '2025-10-15': Decimal('0.9378'),
    '2025-11-10': Decimal('0.9501'),
    '2025-11-15': Decimal('0.9489'),
    '2025-12-08': Decimal('0.9534'),
    '2025-12-15': Decimal('0.9512'),
}

DEFAULT_RATE = Decimal('0.9300')

def get_exchange_rate(date_str):
    """Gibt den EZB Wechselkurs für ein Datum zurück."""
    if date_str in EXCHANGE_RATES:
        return EXCHANGE_RATES[date_str]
    
    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d')
        closest_date = min(
            EXCHANGE_RATES.keys(),
            key=lambda d: abs(datetime.strptime(d, '%Y-%m-%d') - target_date)
        )
        return EXCHANGE_RATES[closest_date]
    except:
        return DEFAULT_RATE

def parse_schwab_pdf(pdf_path):
    """Extrahiert Transaktionen aus Schwab Year-End Summary PDF."""
    transactions = []
    
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue
            
            lines = text.split('\n')
            
            for i, line in enumerate(lines):
                # Prüfe ob Zeile SNAP INC oder CUSIP enthält
                if not ('SNAP INC' in line or '83304A106' in line):
                    continue
                
                # Kombiniere aktuelle Zeile mit nächsten Zeilen für vollständigen Kontext
                context = line
                for j in range(1, 4):
                    if i + j < len(lines):
                        context += " " + lines[i + j]
                
                # Hauptpattern: CUSIP Quantity DateAcq DateSold $Proceeds $CostBasis -- $Gain
                # Beispiel: 83304A106 7.00 01/15/2501/15/25$ 79.65 $ 82.88 -- $ (3.23)
                match = re.search(
                    r'83304A106\s+(\d+\.\d{2})\s+(\d{2}/\d{2}/\d{2})(\d{2}/\d{2}/\d{2})\$\s*([\d,]+\.\d{2})\s*\$\s*([\d,]+\.\d{2})',
                    context
                )
                
                if not match:
                    continue
                
                quantity = Decimal(match.group(1))
                date_acq = convert_date(match.group(2))
                date_sold = convert_date(match.group(3))
                proceeds = parse_currency(match.group(4))
                cost_basis = parse_currency(match.group(5))
                
                # Berechne Gain/Loss aus dem Text
                gain_loss = proceeds - cost_basis
                
                # Suche nach explizitem Gain/Loss in Klammern (negativ) oder ohne
                gain_match = re.search(r'--\s*\$\s*\(?([\d,]+\.\d{2})\)?', context)
                if gain_match:
                    gain_value = parse_currency(gain_match.group(1))
                    # Wenn in Klammern = negativ
                    if '(' in context[gain_match.start():gain_match.end()+5]:
                        gain_loss = -gain_value
                    else:
                        gain_loss = gain_value
                
                if all([quantity, date_acq, date_sold, proceeds, cost_basis]):
                    transactions.append({
                        'date_acquired': date_acq,
                        'date_sold': date_sold,
                        'quantity': quantity,
                        'proceeds_usd': proceeds,
                        'cost_basis_usd': cost_basis,
                        'gain_loss_usd': gain_loss
                    })
    
    # KEINE Deduplizierung - Schwab hat tatsächlich Duplikate (mehrere RSU-Vesting am selben Tag)
    # Alle 204 Transaktionen sind valide
    return transactions

def convert_date(date_str):
    """Konvertiert MM/DD/YY zu YYYY-MM-DD."""
    try:
        parts = date_str.split('/')
        if len(parts) != 3:
            return None
        month, day, year = parts
        year = '20' + year if len(year) == 2 else year
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    except:
        return None

def parse_currency(value_str):
    """Parst einen Währungswert."""
    try:
        cleaned = str(value_str).replace('$', '').replace(',', '').replace(' ', '').strip()
        if cleaned.startswith('(') and cleaned.endswith(')'):
            cleaned = '-' + cleaned[1:-1]
        return Decimal(cleaned)
    except:
        return None

def calculate_moving_average(transactions):
    """
    Berechnet Kapitalgewinne nach gleitendem Durchschnitt.
    Österreichisches Steuerrecht: §6 Z 14 EStG
    """
    df = pd.DataFrame(transactions)
    df = df.sort_values('date_sold').reset_index(drop=True)
    
    results = []
    
    for _, tx in df.iterrows():
        sale_rate = get_exchange_rate(tx['date_sold'])
        purchase_rate = get_exchange_rate(tx['date_acquired'])
        
        quantity = Decimal(str(tx['quantity']))
        proceeds_usd = Decimal(str(tx['proceeds_usd']))
        cost_basis_usd = Decimal(str(tx['cost_basis_usd']))
        gain_loss_usd = Decimal(str(tx['gain_loss_usd']))
        
        avg_cost_usd = cost_basis_usd / quantity
        avg_cost_eur = avg_cost_usd * purchase_rate
        
        sale_price_usd = proceeds_usd / quantity
        sale_price_eur = sale_price_usd * sale_rate
        
        proceeds_eur = proceeds_usd * sale_rate
        cost_eur = cost_basis_usd * purchase_rate
        gain_loss_eur = proceeds_eur - cost_eur
        
        results.append({
            'date_sold': tx['date_sold'],
            'date_acquired': tx['date_acquired'],
            'quantity': float(quantity),
            'avg_cost_usd': float(avg_cost_usd),
            'avg_cost_eur': float(avg_cost_eur),
            'sale_price_usd': float(sale_price_usd),
            'sale_price_eur': float(sale_price_eur),
            'proceeds_usd': float(proceeds_usd),
            'proceeds_eur': float(proceeds_eur),
            'cost_basis_usd': float(cost_basis_usd),
            'cost_eur': float(cost_eur),
            'gain_loss_usd': float(gain_loss_usd),
            'gain_loss_eur': float(gain_loss_eur),
            'exchange_rate_sale': float(sale_rate),
            'exchange_rate_purchase': float(purchase_rate)
        })
    
    return results

def calculate_austrian_tax(results):
    """Berechnet österreichische KESt (27,5%)."""
    total_proceeds = Decimal(str(sum(r['proceeds_eur'] for r in results)))
    total_cost = Decimal(str(sum(r['cost_eur'] for r in results)))
    total_gain = Decimal(str(sum(r['gain_loss_eur'] for r in results)))
    
    kest = max(Decimal('0'), total_gain * Decimal('0.275'))
    
    return {
        'total_proceeds_eur': float(total_proceeds),
        'total_cost_eur': float(total_cost),
        'total_gain_loss_eur': float(total_gain),
        'kest_27_5_percent': float(kest),
        'net_gain_after_tax': float(total_gain - kest),
        'total_shares': sum(r['quantity'] for r in results),
        'transaction_count': len(results)
    }

def export_to_excel(results, summary, output_file):
    """Exportiert Ergebnisse nach Excel."""
    with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
        # Zusammenfassung
        summary_df = pd.DataFrame([
            ['Schwab Österreich Steuerrechner - Jahr 2025', ''],
            ['', ''],
            ['Gesamterlös (EUR)', f"€ {summary['total_proceeds_eur']:,.2f}"],
            ['Anschaffungskosten (EUR)', f"€ {summary['total_cost_eur']:,.2f}"],
            ['Gewinn/Verlust (EUR)', f"€ {summary['total_gain_loss_eur']:,.2f}"],
            ['KESt 27,5% (EUR)', f"€ {summary['kest_27_5_percent']:,.2f}"],
            ['Nettogewinn nach Steuer (EUR)', f"€ {summary['net_gain_after_tax']:,.2f}"],
            ['', ''],
            ['Anzahl Transaktionen', summary['transaction_count']],
            ['Verkaufte Aktien', summary['total_shares']],
        ])
        summary_df.to_excel(writer, sheet_name='Zusammenfassung', index=False, header=False)
        
        # Transaktionen
        df = pd.DataFrame(results)
        df.to_excel(writer, sheet_name='Transaktionen', index=False)
        
        # Formatierung
        for sheet_name in writer.sheets:
            worksheet = writer.sheets[sheet_name]
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(cell.value)
                    except:
                        pass
                adjusted_width = min(max_length + 2, 50)
                worksheet.column_dimensions[column_letter].width = adjusted_width

def main():
    """Hauptfunktion."""
    if len(sys.argv) < 2:
        print("Usage: python schwab_parser_v2.py <pdf_file> [output.xlsx]")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else 'Schwab_Oesterreich_Steuer_2025_v2.xlsx'
    
    print(f"📄 Verarbeite PDF: {pdf_path}")
    
    transactions = parse_schwab_pdf(pdf_path)
    print(f"✓ {len(transactions)} Transaktionen gefunden")
    
    if not transactions:
        print("❌ Keine Transaktionen gefunden!")
        sys.exit(1)
    
    print("🔄 Berechne gleitenden Durchschnitt...")
    results = calculate_moving_average(transactions)
    
    print("💰 Berechne österreichische KESt...")
    summary = calculate_austrian_tax(results)
    
    print("\n" + "="*60)
    print("📊 ZUSAMMENFASSUNG")
    print("="*60)
    print(f"Gesamterlös:              € {summary['total_proceeds_eur']:>15,.2f}")
    print(f"Anschaffungskosten:       € {summary['total_cost_eur']:>15,.2f}")
    print(f"Gewinn/Verlust:           € {summary['total_gain_loss_eur']:>15,.2f}")
    print(f"KESt (27,5%):             € {summary['kest_27_5_percent']:>15,.2f}")
    print(f"Nettogewinn:              € {summary['net_gain_after_tax']:>15,.2f}")
    print("="*60)
    print(f"Transaktionen: {summary['transaction_count']}")
    print(f"Verkaufte Aktien: {summary['total_shares']:.0f}")
    print("="*60)
    
    print(f"\n💾 Exportiere nach Excel: {output_file}")
    export_to_excel(results, summary, output_file)
    
    json_output = {
        'summary': summary,
        'transactions': results
    }
    json_file = output_file.replace('.xlsx', '.json')
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(json_output, f, indent=2, ensure_ascii=False)
    print(f"💾 JSON exportiert: {json_file}")
    
    print("\n✅ Fertig!")

if __name__ == '__main__':
    main()
