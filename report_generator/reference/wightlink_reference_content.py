from __future__ import annotations

from typing import Any


DEFAULT_WIGHTLINK_MANUAL_INPUTS: dict[str, Any] = {
    "agenda": [
        "Trends",
        "Auction Insights",
        "Performance",
        "Audience Insights",
        "Competitor Analysis",
        "Opportunities",
        "SEO",
    ],
    "trends": {
        "title": "Google Trends - Isle Of Wight Ferry",
        "fallback_bullets": [
            "No Google Trends CSV was supplied for this report.",
            "Upload one or more Google Trends exports to populate the trend chart and narrative.",
        ],
    },
    "auction": {
        "generic": {
            "title": "Auction Insights - Generic",
            "source_note": "Source: Google Ads Auction Insights",
            "bullets": [
                "In Q3, we saw 4 competitors in the market. This is slightly higher than last year when we saw 3 competitors in the market.",
                "Red Funnel overlap us 61% of the time, claiming the top spot 31% of the time. This suggests they are still investing heavily on generic activity.",
                "Direct Ferries overlap us 77% of the time, appearing in the top spot 28% of the time. This aggressive approach from competitors impacts our CPCs.",
                "Both Direct Ferries and Red Funnel had higher impression share in July than they did in September, suggesting they have increased budget during peak.",
            ],
            "table_rows": [
                {"Display URL domain": "You", "Impression share": "57.65%", "Overlap rate": "--", "Position above rate": "--", "Top of page rate": "92.29%", "Abs. Top of page rate": "34.54%", "Outranking share": "--"},
                {"Display URL domain": "directferries.co.uk", "Impression share": "54.55%", "Overlap rate": "77.04%", "Position above rate": "43.28%", "Top of page rate": "85.96%", "Abs. Top of page rate": "28.24%", "Outranking share": "38.43%"},
                {"Display URL domain": "redfunnel.co.uk", "Impression share": "43.77%", "Overlap rate": "61.07%", "Position above rate": "50.40%", "Top of page rate": "87.99%", "Abs. Top of page rate": "30.68%", "Outranking share": "39.91%"},
                {"Display URL domain": "openferry.com", "Impression share": "36.64%", "Overlap rate": "54.61%", "Position above rate": "37.91%", "Top of page rate": "73.09%", "Abs. Top of page rate": "26.15%", "Outranking share": "45.71%"},
                {"Display URL domain": "awayresorts.co.uk", "Impression share": "11.97%", "Overlap rate": "16.81%", "Position above rate": "21.97%", "Top of page rate": "43.28%", "Abs. Top of page rate": "15.55%", "Outranking share": "55.52%"},
            ],
        },
        "brand": {
            "title": "Auction Insights - Brand",
            "source_note": "Source: Google Ads Auction Insights",
            "bullets": [
                "Throughout the quarter we saw 4 main competitors in the market.",
                "Red Funnel were overlapping us 43% of the time, claiming the top spot 25% of the time.",
                "Comparing YoY, Red Funnel had an increased presence versus the reference period.",
            ],
            "table_rows": [
                {"Display URL domain": "You", "Impression share": "80.06%", "Overlap rate": "--", "Position above rate": "--", "Top of page rate": "91.08%", "Abs. Top of page rate": "74.04%", "Outranking share": "--"},
                {"Display URL domain": "redfunnel.co.uk", "Impression share": "35.74%", "Overlap rate": "42.52%", "Position above rate": "26.97%", "Top of page rate": "84.11%", "Abs. Top of page rate": "25.42%", "Outranking share": "70.88%"},
                {"Display URL domain": "awayresorts.co.uk", "Impression share": "12.68%", "Overlap rate": "15.36%", "Position above rate": "13.38%", "Top of page rate": "62.10%", "Abs. Top of page rate": "12.60%", "Outranking share": "78.41%"},
                {"Display URL domain": "adamvacations.com", "Impression share": "< 10%", "Overlap rate": "11.67%", "Position above rate": "11.55%", "Top of page rate": "42.10%", "Abs. Top of page rate": "9.31%", "Outranking share": "78.98%"},
                {"Display URL domain": "directferries.co.uk", "Impression share": "< 10%", "Overlap rate": "11.56%", "Position above rate": "15.59%", "Top of page rate": "72.42%", "Abs. Top of page rate": "13.05%", "Outranking share": "78.61%"},
            ],
        },
    },
    "audience": {
        "generic_location": {
            "title": "Audience Insights - Generics Location Targeting",
            "subtitle": "(Q3 2025 Google only)",
            "bullets": [
                "As you would expect the majority of our traffic comes from the South of England, and our investment mirrors that.",
                "The Isle of Wight has the highest CVR, which is consistent with the reference deck.",
            ],
            "table_rows": [
                {"County (Matched)": "Hampshire", "Impr.": "183,102", "Clicks": "42,382", "CTR": "23.15%", "CPC": "£0.28", "Cost": "£11,815.56", "Conversions": "6,109", "CPA": "£1.93", "CVR": "14.41%"},
                {"County (Matched)": "Greater London", "Impr.": "197,766", "Clicks": "34,271", "CTR": "17.33%", "CPC": "£0.31", "Cost": "£10,716.38", "Conversions": "4,806", "CPA": "£2.23", "CVR": "14.02%"},
                {"County (Matched)": "Isle of Wight", "Impr.": "79,349", "Clicks": "16,790", "CTR": "21.16%", "CPC": "£0.45", "Cost": "£7,512.68", "Conversions": "3,082", "CPA": "£2.44", "CVR": "18.35%"},
                {"County (Matched)": "West Sussex", "Impr.": "38,257", "Clicks": "10,519", "CTR": "27.50%", "CPC": "£0.27", "Cost": "£2,868.07", "Conversions": "1,462", "CPA": "£1.96", "CVR": "13.90%"},
                {"County (Matched)": "Surrey", "Impr.": "32,705", "Clicks": "7,543", "CTR": "23.06%", "CPC": "£0.31", "Cost": "£2,306.64", "Conversions": "1,105", "CPA": "£2.09", "CVR": "14.64%"},
            ],
        },
        "generic_device": {
            "title": "Audience Insights - Generics Device Performance",
            "subtitle": "(Q3 2025 Google only)",
            "bullets": [
                "The majority of impressions and clicks are from mobile, and investment continues to follow that demand.",
                "Desktop had the strongest CVR and a similar CPA to the other devices, so it remained an efficient source of conversion volume.",
            ],
            "table_rows": [
                {"Device": "Mobile phones", "Impr.": "731,128", "Clicks": "150,483", "CTR": "20.58%", "Avg. CPC": "£0.25", "Cost": "£38,142.36", "Conversions": "18,404", "CPA": "£2.07", "CVR": "12.23%", "Search impr. share": "58.55%"},
                {"Device": "Computers", "Impr.": "182,096", "Clicks": "31,697", "CTR": "17.41%", "Avg. CPC": "£0.50", "Cost": "£15,828.35", "Conversions": "7,720", "CPA": "£2.05", "CVR": "24.36%", "Search impr. share": "56.23%"},
                {"Device": "Tablets", "Impr.": "26,557", "Clicks": "5,437", "CTR": "20.47%", "Avg. CPC": "£0.28", "Cost": "£1,518.94", "Conversions": "641", "CPA": "£2.37", "CVR": "11.79%", "Search impr. share": "51.92%"},
            ],
        },
        "brand_location": {
            "title": "Audience Insights - Brand Location Targeting",
            "subtitle": "(Q3 2025 Google only)",
            "bullets": [
                "Brand location splits broadly mirror the generics footprint.",
                "The Isle of Wight remained the strongest conversion area within the reference deck.",
            ],
            "table_rows": [
                {"County (Matched)": "Isle of Wight", "Impr.": "98,611", "Clicks": "53,797", "CTR": "54.55%", "CPC": "£0.12", "Cost": "£6,367.53", "Conversions": "13,790", "CPA": "£0.46", "CVR": "25.63%"},
                {"County (Matched)": "Hampshire", "Impr.": "59,499", "Clicks": "31,968", "CTR": "53.73%", "CPC": "£0.11", "Cost": "£3,385.37", "Conversions": "5,906", "CPA": "£0.57", "CVR": "18.47%"},
                {"County (Matched)": "Greater London", "Impr.": "55,381", "Clicks": "28,361", "CTR": "51.21%", "CPC": "£0.11", "Cost": "£3,012.38", "Conversions": "5,743", "CPA": "£0.52", "CVR": "20.25%"},
                {"County (Matched)": "West Sussex", "Impr.": "12,537", "Clicks": "7,144", "CTR": "56.98%", "CPC": "£0.10", "Cost": "£728.09", "Conversions": "1,311", "CPA": "£0.56", "CVR": "18.34%"},
                {"County (Matched)": "Surrey", "Impr.": "10,609", "Clicks": "6,015", "CTR": "56.70%", "CPC": "£0.11", "Cost": "£661.48", "Conversions": "1,259", "CPA": "£0.53", "CVR": "20.93%"},
            ],
        },
        "brand_device": {
            "title": "Audience Insights - Brand Device Performance",
            "subtitle": "(Q3 2025 Google only)",
            "bullets": [
                "Mobile still drove most brand impressions and clicks.",
                "Desktop conversion strength was materially higher and accounted for a large share of brand conversions in the reference deck.",
            ],
            "table_rows": [
                {"Device": "Mobile phones", "Impr.": "255,202", "Clicks": "136,063", "CTR": "53.32%", "Avg. CPC": "£0.11", "Cost": "£15,067.60", "Conversions": "21,464", "CPA": "£0.70", "CVR": "15.78%", "Search impr. share": "80.02%"},
                {"Device": "Computers", "Impr.": "79,642", "Clicks": "42,721", "CTR": "53.64%", "Avg. CPC": "£0.12", "Cost": "£5,148.75", "Conversions": "16,466", "CPA": "£0.31", "CVR": "38.54%", "Search impr. share": "81.58%"},
                {"Device": "Tablets", "Impr.": "10,856", "Clicks": "5,424", "CTR": "49.96%", "Avg. CPC": "£0.13", "Cost": "£716.63", "Conversions": "1,126", "CPA": "£0.64", "CVR": "20.75%", "Search impr. share": "72.06%"},
            ],
        },
    },
    "tests": {
        "brand_tcpa": {
            "title": "Brand tCPA Test",
            "bullets": [
                "From an efficiency standpoint, this test was not successful.",
                "The treatment arm improved conversion rate but at a materially higher CPC and cost per conversion.",
                "Several of the efficiency movements were statistically significant in the reference analysis.",
            ],
            "table_rows": [
                {"Arm": "Control", "Impr": "34,522", "Clicks": "15,926", "CTR": "46.13%", "CPC": "£0.17", "Cost": "£2,644.09", "Conversions": "2,244", "Cost/Conversion": "£1.18", "Conversion Value": "£208,138.19", "Conversion Value/Cost": "78.72", "CVR": "14.09%"},
                {"Arm": "Treatment", "Impr": "29,996", "Clicks": "14,074", "CTR": "46.92%", "CPC": "£0.25", "Cost": "£3,473.78", "Conversions": "2,333", "Cost/Conversion": "£1.49", "Conversion Value": "£226,417.79", "Conversion Value/Cost": "65.18", "CVR": "16.58%"},
                {"Arm": "Difference (%)", "Impr": "-13%", "Clicks": "-12%", "CTR": "2%", "CPC": "49%", "Cost": "31%", "Conversions": "4%", "Cost/Conversion": "26%", "Conversion Value": "9%", "Conversion Value/Cost": "-17%", "CVR": "18%"},
            ],
        },
        "target_roas": {
            "title": "Target ROAS Test",
            "bullets": [
                "The control arm performed significantly better, so the test was ended.",
                "Treatment drove fewer conversions and lower conversion value at a higher CPC.",
                "Cost per conversion was significantly higher in the treatment arm.",
            ],
            "table_rows": [
                {"Arm": "Control", "Impr": "6,482", "Clicks": "1,589", "CTR": "24.51%", "CPC": "£0.32", "Cost": "£502.79", "Conversions": "205", "Cost/Conversion": "£2.45", "Conversion Value": "£20,367.47", "Conversion Value/Cost": "40.51", "CVR": "12.91%"},
                {"Arm": "Treatment", "Impr": "5,119", "Clicks": "1,307", "CTR": "25.53%", "CPC": "£0.39", "Cost": "£509.29", "Conversions": "130", "Cost/Conversion": "£3.91", "Conversion Value": "£12,118.54", "Conversion Value/Cost": "23.79", "CVR": "9.96%"},
                {"Arm": "Difference (%)", "Impr": "-21%", "Clicks": "-18%", "CTR": "4%", "CPC": "22%", "Cost": "1%", "Conversions": "-37%", "Cost/Conversion": "60%", "Conversion Value": "-41%", "Conversion Value/Cost": "-41%", "CVR": "-23%"},
            ],
        },
    },
    "seo": {
        "overview_title": "Organic Keywords (Non Brand)",
        "overview_bullets": [
            "From the 25,933 keywords, 20,262 are non-branded terms.",
            "The reference deck notes a slight reduction in non-brand keyword count, largely in secondary terms.",
        ],
        "summary_title": "YOY Clicks reduced, but impact small",
        "summary_bullets": [
            "Impressions were up 31%, suggesting broader visibility across more non-brand terms.",
            "Clicks were down 20% YoY in the reference deck, with weaker CTR consistent with visibility at lower positions.",
            "AI and LLM behaviour should only be referenced when it is explicitly provided in the manual content, which it is here.",
        ],
    },
    "opportunities": {
        "title": "Live and upcoming tests/builds",
        "bullets": [
            "Search Term Analyser - Running successfully on the account",
            "Ad copy testing using AI - Currently running (not significant yet)",
            "AI Max - Currently testing",
            "Ad customisers - price insertion (on hold)",
            "Predictive budget allocation",
            "Competitor Ad Analysis",
        ],
    },
    "competitor_analysis": {
        "title": "Competitor Analysis - Red Funnel",
        "bullets": [
            "Mix of formats can be seen across Search, Video and Images.",
            "Creative examples are heavily price-led, including single, return and percentage-off messaging.",
            "The reference deck suggests Performance Max activity promoting Isle of Wight activities.",
            "DSA appears to have been stopped.",
            "Ad extension usage appears limited, mainly image extensions.",
            "Ad Transparency Tool HERE",
        ],
    },
}
