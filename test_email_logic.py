import sys
import unittest
from bs4 import BeautifulSoup
from typing import Optional

# Mocking variables and functions if necessary, or just imports
# Since rss_daily_summary allows importing functions
from rss_daily_summary import make_email_safe_fragment

class TestEmailLogic(unittest.TestCase):
    def test_hr_handling(self):
        # Input HTML similar to what wrap_html produces
        html_input = """
        <div class="container">
          <div class="bar"><button>Print</button></div>
          <h1>HCLS — Weekly Summary</h1>
          <div>Period: 2025-01-01</div>
          <hr style="height:1px;background:#222;border:0;margin:1rem 0;">
          <p>Some content</p>
        </div>
        """
        
        safe = make_email_safe_fragment(html_input)
        print(f"Safe HTML: {safe}")
        
        # Check if hr is present and styled
        self.assertIn("<hr", safe)
        self.assertIn("border-top:1px solid #e5e7eb", safe)
        
        # Check if H1 is preserved (since we need it there, but we removed the DUPLICATE from the outer template)
        self.assertIn("HCLS — Weekly Summary", safe)
        
        # Check if scripts/buttons removed
        self.assertNotIn("Print", safe)
        self.assertNotIn("button", safe)

if __name__ == '__main__':
    unittest.main()
