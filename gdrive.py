"""Google Drive integration via rclone.

Provides a reusable client for uploading, downloading, listing, and sharing
files on Google Drive.  Uses rclone (already installed and OAuth-configured)
so it works with personal Google accounts without service account limitations.

For headless endpoints: deploy a copy of the rclone.conf with the OAuth token.
The token auto-refreshes via the refresh_token grant.
"""

import json
import os
import subprocess
import threading
import time


class GDriveClient:
    """Google Drive client using rclone."""

    def __init__(self, remote='gdrive', folder_path='', rclone_config=None):
        """Init with rclone remote name and optional folder path.

        Args:
            remote: rclone remote name (default 'gdrive').
            folder_path: path within the remote (e.g. 'radio-gateway').
            rclone_config: path to rclone.conf (default: ~/.config/rclone/rclone.conf).
        """
        self._remote = remote
        self._folder_path = folder_path.strip('/')
        self._rclone_config = rclone_config
        self._lock = threading.Lock()

        # Verify rclone is available
        try:
            r = subprocess.run(['rclone', 'version'], capture_output=True,
                               text=True, timeout=5)
            ver = r.stdout.split('\n')[0] if r.returncode == 0 else '?'
        except FileNotFoundError:
            raise RuntimeError("rclone not installed")

        # Verify remote exists
        try:
            r = subprocess.run(self._cmd('lsd', self._rpath('')),
                               capture_output=True, text=True, timeout=15)
            if r.returncode != 0:
                raise RuntimeError(f"rclone remote '{remote}' failed: {r.stderr.strip()}")
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"rclone timeout accessing remote '{remote}'")

        print(f"  [GDrive] Connected via rclone ({ver}) → {remote}:{folder_path}")

    def _rpath(self, path=''):
        """Build rclone remote path."""
        parts = [self._remote + ':']
        if self._folder_path:
            parts.append(self._folder_path)
        if path:
            parts.append(path.lstrip('/'))
        return '/'.join(parts) if len(parts) > 1 else parts[0]

    def _cmd(self, *args):
        """Build rclone command with optional config flag."""
        cmd = ['rclone']
        if self._rclone_config:
            cmd += ['--config', self._rclone_config]
        cmd.extend(args)
        return cmd

    def _run(self, *args, timeout=30):
        """Run rclone command, return (stdout, stderr, returncode)."""
        cmd = self._cmd(*args)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout, r.stderr, r.returncode

    # -- File operations ----------------------------------------------------

    def upload_file(self, local_path, drive_name=None, subfolder=None):
        """Upload a local file to Google Drive.  Returns True on success."""
        dest = self._rpath(subfolder or '')
        cmd = self._cmd('copyto', local_path,
                        dest + '/' + (drive_name or os.path.basename(local_path)))
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return r.returncode == 0

    def download_file(self, drive_name, local_path, subfolder=None):
        """Download a file from Google Drive.  Returns True on success."""
        src = self._rpath(subfolder or '') + '/' + drive_name
        os.makedirs(os.path.dirname(local_path) or '.', exist_ok=True)
        cmd = self._cmd('copyto', src, local_path)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return r.returncode == 0

    def read_json(self, drive_name, subfolder=None):
        """Read and parse a JSON file from Drive.  Returns dict or None."""
        src = self._rpath(subfolder or '') + '/' + drive_name
        stdout, stderr, rc = self._run('cat', src)
        if rc != 0:
            return None
        try:
            return json.loads(stdout)
        except (json.JSONDecodeError, ValueError):
            return None

    def write_json(self, data, drive_name, subfolder=None):
        """Write a dict as a JSON file to Drive.  Returns True on success."""
        import tempfile
        tf = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        try:
            json.dump(data, tf, indent=2)
            tf.close()
            return self.upload_file(tf.name, drive_name, subfolder)
        finally:
            try:
                os.unlink(tf.name)
            except Exception:
                pass

    # -- Folder operations --------------------------------------------------

    def list_files(self, subfolder=None):
        """List files in a folder.  Returns list of file metadata dicts."""
        path = self._rpath(subfolder or '')
        stdout, stderr, rc = self._run('lsjson', path)
        if rc != 0:
            return []
        try:
            items = json.loads(stdout)
            return [{
                'name': f['Name'],
                'size': str(f.get('Size', 0)),
                'mimeType': f.get('MimeType', ''),
                'modifiedTime': f.get('ModTime', ''),
                'isDir': f.get('IsDir', False),
            } for f in items]
        except (json.JSONDecodeError, ValueError):
            return []

    def create_folder(self, name, subfolder=None):
        """Create a folder on Drive.  Returns True on success."""
        path = self._rpath(subfolder or '') + '/' + name
        _, _, rc = self._run('mkdir', path)
        return rc == 0

    def ensure_folder(self, name, subfolder=None):
        """Ensure a subfolder exists.  Returns True."""
        path = self._rpath(subfolder or '') + '/' + name
        self._run('mkdir', path)
        return True

    # -- Sharing ------------------------------------------------------------

    def get_link(self, drive_name, subfolder=None):
        """Get a shareable link for a file. Returns URL or None."""
        path = self._rpath(subfolder or '') + '/' + drive_name
        stdout, stderr, rc = self._run('link', path)
        if rc == 0 and stdout.strip():
            return stdout.strip()
        return None

    # -- Convenience --------------------------------------------------------

    def file_exists(self, name, subfolder=None):
        """Check if a file exists by name.  Returns True/False."""
        files = self.list_files(subfolder)
        return any(f['name'] == name for f in files)

    def delete_file(self, drive_name, subfolder=None):
        """Delete a file from Drive."""
        path = self._rpath(subfolder or '') + '/' + drive_name
        _, _, rc = self._run('deletefile', path)
        return rc == 0

    def get_status(self):
        """Return status dict for web UI / MCP."""
        result = {
            'configured': True,
            'remote': self._remote,
            'folder_path': self._folder_path,
        }
        # Test access
        try:
            stdout, stderr, rc = self._run('about', f'{self._remote}:',
                                           '--json')
            if rc == 0:
                about = json.loads(stdout)
                result['authenticated'] = True
                result['total_bytes'] = about.get('total', 0)
                result['used_bytes'] = about.get('used', 0)
                result['free_bytes'] = about.get('free', 0)
                result['folder_accessible'] = True
            else:
                result['authenticated'] = False
                result['folder_accessible'] = False
                result['folder_error'] = stderr.strip()
        except Exception as e:
            result['authenticated'] = False
            result['folder_accessible'] = False
            result['folder_error'] = str(e)
        return result
