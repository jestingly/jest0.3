#!/usr/bin/env python3
# Build bundled JEST assets, generate a sandbox page, and refresh legacy includes.
import os
import re
import subprocess
from pathlib import Path

# -------------------------------
# Configuration
# -------------------------------
# Asset and page locations, relative to the directory containing this script.
BASE_DIR           = Path(__file__).resolve().parent
INDEX_PATH         = BASE_DIR / 'index.php'
SANDBOX_PATH       = BASE_DIR / 'index.sandbox.php'
ORDER_PATH         = BASE_DIR / 'load_order.txt'
INCLUDES_PATH      = BASE_DIR / 'includes/js.php'
CSS_INCLUDES_PATH  = BASE_DIR / 'includes/css.php'
BUNDLE_JS_PATH     = BASE_DIR / 'js/bundle.min.js'
TEMP_COMBINED_JS   = BASE_DIR / 'js/bundle.raw.js'
BUNDLE_CSS_PATH    = BASE_DIR / 'css/bundle.min.css'
TEMP_COMBINED_CSS  = BASE_DIR / 'css/bundle.raw.css'
INDEX_HTML         = BASE_DIR / 'index.html'

# -------------------------------
# Utility Functions
# -------------------------------
# Interpret the load-order file, retaining local and remote asset groups.
def read_load_order():
	local_js, remote_js, local_css, remote_css = [], [], [], []
	with ORDER_PATH.open('r', encoding='utf-8') as source:
		for raw_line in source:
			line = raw_line.strip()
			if not line or line.startswith('#'):
				continue

			# Remove the scope prefix before determining the file extension.
			scope, separator, path = line.partition(':')
			if separator and scope.lower() in ('local', 'remote'):
				path = path.strip()
				scope = scope.lower()
			else:
				path, scope = line, 'both'

			# Ignore query strings and fragments when identifying the asset type.
			asset_path = path.split('?', 1)[0].split('#', 1)[0].lower()
			if asset_path.endswith('.js'):
				local_group, remote_group = local_js, remote_js
			elif asset_path.endswith('.css'):
				local_group, remote_group = local_css, remote_css
			else:
				print(f'[!] Unknown asset type: {line} — Skipping.')
				continue

			if scope in ('local', 'both'):
				local_group.append(path)
			if scope in ('remote', 'both'):
				remote_group.append(path)

	return local_js, remote_js, local_css, remote_css


# Combine the requested files in order; fail rather than publish incomplete bundles.
def combine_scripts_to_raw(scripts, output_path):
	output_path.parent.mkdir(parents=True, exist_ok=True)
	with output_path.open('w', encoding='utf-8') as output:
		for script in scripts:
			# The bundle reads files from disk, not from remote HTTP addresses.
			asset_path = script.split('?', 1)[0].split('#', 1)[0]
			path = BASE_DIR / asset_path
			if not path.is_file():
				raise FileNotFoundError(f'Missing bundle input: {script}')

			output.write(f'/* --- BEGIN {script} --- */\n')
			with path.open('r', encoding='utf-8') as source:
				lines = source.readlines()
				if lines and lines[0].strip().startswith("console.log( 'jestAlert: js"):
					lines = lines[1:]
				output.writelines(lines)
			output.write('\n')
	print(f'[✓] Combined raw file written: {output_path.relative_to(BASE_DIR)}')


# Execute a build command and halt if the output cannot be generated.
def run_build(command, label):
	subprocess.run([str(argument) for argument in command], check=True, cwd=BASE_DIR)
	print(f'[✓] {label}')


# Minify the combined JavaScript file.
def minify_js():
	run_build(['terser', TEMP_COMBINED_JS, '-o', BUNDLE_JS_PATH, '--compress'], 'Minified JS')


# Obfuscate the minified JavaScript bundle.
def obfuscate_js():
	run_build(['javascript-obfuscator', BUNDLE_JS_PATH, '--output', BUNDLE_JS_PATH], 'Obfuscated JS')


# Minify the combined CSS file.
def minify_css():
	run_build(['cleancss', '-o', BUNDLE_CSS_PATH, TEMP_COMBINED_CSS], 'Minified CSS')


# Remove only standalone PHP include blocks targeting the legacy asset includes.
def remove_legacy_php_include(match):
	block = match.group(0)
	# Preserve blocks containing unrelated PHP logic, rather than deleting it.
	standalone = re.fullmatch(
		r'''<\?(?:php\s+|=\s*)?(?:(?:include|include_once|require|require_once)\s*\(?\s*['"][^'"]*includes/(?:js|css)\.php['"]\s*\)?\s*;\s*)+\?>''',
		block.strip(),
		flags=re.IGNORECASE | re.DOTALL,
	)
	return '' if standalone else block


# Generate the sandbox from index.php without allowing regexes to cross PHP blocks.
def rewrite_index_to_sandbox():
	html = INDEX_PATH.read_text(encoding='utf-8')

	# Remove external script tags while preserving inline JavaScript.
	html = re.sub(
		r'<script\b(?=[^>]*\bsrc\s*=)[^>]*>\s*</script\s*>',
		'', html, flags=re.IGNORECASE | re.DOTALL,
	)

	# Remove stylesheet links, but leave icons and unrelated links intact.
	def remove_stylesheet(match):
		tag = match.group(0)
		return '' if re.search(r'\brel\s*=\s*[\'"]?stylesheet\b', tag, re.IGNORECASE) else tag

	html = re.sub(r'<link\b[^>]*>', remove_stylesheet, html, flags=re.IGNORECASE)

	# Crucial: match each PHP block independently, never across intervening HTML.
	html = re.sub(r'<\?php\b(?:(?!\?>)[\s\S])*\?>', remove_legacy_php_include, html, flags=re.IGNORECASE)

	# Inject exactly one reference to each generated bundle.
	html, css_count = re.subn(
		r'</head\s*>', '\t<link rel="stylesheet" href="css/bundle.min.css">\n</head>',
		html, count=1, flags=re.IGNORECASE,
	)
	html, js_count = re.subn(
		r'</body\s*>', '\t<script src="js/bundle.min.js"></script>\n</body>',
		html, count=1, flags=re.IGNORECASE,
	)
	if css_count != 1 or js_count != 1:
		raise ValueError('Expected closing </head> and </body> tags in index.php; sandbox not written.')

	SANDBOX_PATH.write_text(html, encoding='utf-8')
	print(f'[✓] Sandbox created: {SANDBOX_PATH.relative_to(BASE_DIR)}')


# Refresh the PHP include file with local asset references.
def write_php_includes(scripts, target_path, tag_format):
	target_path.parent.mkdir(parents=True, exist_ok=True)
	target_path.write_text(''.join(tag_format.format(script) + '\n' for script in scripts), encoding='utf-8')
	print(f'[✓] Includes written: {target_path.relative_to(BASE_DIR)}')


# Replace a named HTML comment block without modifying unrelated content.
def replace_block(html, marker, new_lines):
	pattern = re.compile(
		rf'<!--\s*{re.escape(marker)}\s+begin\s*-->[\s\S]*?<!--\s*{re.escape(marker)}\s+end\s*-->',
		flags=re.IGNORECASE,
	)
	replacement = f'<!-- {marker} begin -->\n{new_lines.rstrip()}\n<!-- {marker} end -->'
	return pattern.sub(lambda match: replacement, html)


# Refresh the marked local CSS and JS blocks in index.html, when it exists.
def inject_includes(local_js, local_css):
	if not INDEX_HTML.is_file():
		print('[•] index.html not found; skipping HTML include injection.')
		return

	html = INDEX_HTML.read_text(encoding='utf-8')
	css_tags = '\n'.join(f'<link rel="stylesheet" href="{path}">' for path in local_css)
	js_tags = '\n'.join(f'<script src="{path}"></script>' for path in local_js)
	html = replace_block(html, 'jest_css', css_tags)
	html = replace_block(html, 'jest_js', js_tags)
	INDEX_HTML.write_text(html, encoding='utf-8')
	print('[✓] Injected local CSS/JS into index.html')


# Build all assets first, then publish sandbox and development include references.
def main():
	print('== JEST BUNDLE SCRIPT (JS + CSS, LEGACY INCLUDES) ==')
	local_js, remote_js, local_css, remote_css = read_load_order()
	print(f'[•] {len(remote_js)} remote JS (bundled)')
	print(f'[•] {len(local_js)} local JS (php-include)')
	print(f'[•] {len(remote_css)} remote CSS (bundled)')
	print(f'[•] {len(local_css)} local CSS (php-include)')

	combine_scripts_to_raw(remote_js, TEMP_COMBINED_JS)
	minify_js()
	obfuscate_js()
	combine_scripts_to_raw(remote_css, TEMP_COMBINED_CSS)
	minify_css()
	rewrite_index_to_sandbox()
	write_php_includes(local_js, INCLUDES_PATH, '<script src="{}"></script>')
	write_php_includes(local_css, CSS_INCLUDES_PATH, '<link rel="stylesheet" href="{}">')
	inject_includes(local_js, local_css)


# Run the build only when executed as a script.
if __name__ == '__main__':
	main()
