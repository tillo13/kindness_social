from re        import findall, search
from json      import load, dump, loads
from base64    import b64decode
from typing    import Optional
from curl_cffi import requests
from grok_core import Utils
from os        import path

class Parser:
    
    mapping: dict = {}
    _mapping_loaded: bool = False
    
    grok_mapping: list = []
    _grok_mapping_loaded: bool = False
    
    _mappings_dir: str = path.join(path.dirname(path.dirname(__file__)), 'mappings')

    @classmethod
    def _load__xsid_mapping(cls):
        txid_path = path.join(cls._mappings_dir, 'txid.json')
        if not cls._mapping_loaded and path.exists(txid_path):
            with open(txid_path, 'r') as f:
                cls.mapping = load(f)
            cls._mapping_loaded = True

    @classmethod
    def _load_grok_mapping(cls):
        grok_path = path.join(cls._mappings_dir, 'grok.json')
        if not cls._grok_mapping_loaded and path.exists(grok_path):
            with open(grok_path, 'r') as f:
                cls.grok_mapping = load(f)
            cls._grok_mapping_loaded = True
    
    @staticmethod
    def parse_values(html: str, loading: int = 0, scriptId: str = "") -> tuple[str, Optional[str]]:

        Parser._load__xsid_mapping()
        
        d_values = loads(findall(r'\[\[{"color".*?}\]\]', html)[0])[loading]
        svg_data = "M 10,30 C" + " C".join(
            f" {item['color'][0]},{item['color'][1]} {item['color'][2]},{item['color'][3]} {item['color'][4]},{item['color'][5]}"
            f" h {item['deg']}"
            f" s {item['bezier'][0]},{item['bezier'][1]} {item['bezier'][2]},{item['bezier'][3]}"
            for item in d_values
        )
        
        if scriptId:
            
            if scriptId == "ondemand.s":
                script_link: str = 'https://abs.twimg.com/responsive-web/client-web/ondemand.s.' + Utils.between(html, f'"{scriptId}":"', '"') + 'a.js'
            elif scriptId.startswith('http'):
                script_link: str = scriptId
            else:
                script_link: str = f'https://cdn.grok.com/_next/{scriptId}'

            if script_link in Parser.mapping:
                numbers: list = Parser.mapping[script_link]

            else:
                script_content: str = requests.get(script_link, impersonate="chrome136", timeout=10).text
                numbers: list = [int(x) for x in findall(r'x\[(\d+)\]\s*,\s*16', script_content)]
                Parser.mapping[script_link] = numbers
                mappings_dir = path.join(path.dirname(path.dirname(__file__)), 'mappings')
                try:
                    with open(path.join(mappings_dir, 'txid.json'), 'w') as f:
                        dump(Parser.mapping, f)
                except Exception:
                    pass

            return svg_data, numbers

        else:
            return svg_data

    
    @staticmethod
    def get_anim(html:  str, verification: str = "grok-site-verification") -> tuple[str, int]:
        
        verification_token: str = Utils.between(html, f'"name":"{verification}","content":"', '"')
        array: list = list(b64decode(verification_token))
        anim: int = int(array[5] % 4)

        return verification_token, anim
    
    @staticmethod
    def parse_grok(scripts: list) -> tuple[list, str]:
        
        Parser._load_grok_mapping()
        
        for index in Parser.grok_mapping:
            if index.get("action_script") in scripts:
                return index["actions"], index["xsid_script"]
            
        script_content1 = None
        script_content2 = None
        action_script = None

        for script in scripts:
            # Handle both relative and absolute URLs
            url = script if script.startswith('http') else f'https://grok.com{script}'
            try:
                content: str = requests.get(url, impersonate="chrome136", timeout=10).text
            except Exception:
                continue
            if "anonPrivateKey" in content:
                script_content1 = content
                action_script = script
            elif "880932)" in content:
                script_content2 = content

        if not script_content1:
            raise RuntimeError("Could not find anonPrivateKey script - grok.com may have updated")
        if not script_content2:
            raise RuntimeError("Could not find xsid script (880932) - grok.com may have updated")

        actions: list = findall(r'createServerReference\)\("([a-f0-9]+)"', script_content1)
        xsid_match = search(r'"(static/chunks/[^"]+\.js)"[^}]*?\(880932\)', script_content2)
        if not xsid_match:
            # Try alternate pattern for CDN URLs
            xsid_match = search(r'"([^"]*static/chunks/[^"]+\.js)"', script_content2)
        xsid_script: str = xsid_match.group(1) if xsid_match else None

        if actions and xsid_script:
            mappings_dir = path.join(path.dirname(path.dirname(__file__)), 'mappings')
            Parser.grok_mapping.append({
                "xsid_script": xsid_script,
                "action_script": action_script,
                "actions": actions
            })

            try:
                with open(path.join(mappings_dir, 'grok.json'), 'w') as f:
                    dump(Parser.grok_mapping, f, indent=2)
            except Exception:
                pass

            return actions, xsid_script
        else:
            raise RuntimeError(f"Failed to parse scripts: actions={len(actions) if actions else 0}, xsid={bool(xsid_script)}")
        
        