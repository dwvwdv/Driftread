import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { MatDialog } from '@angular/material/dialog';
import { firstValueFrom } from 'rxjs';
import { UpdateDialogComponent } from '../components/update-dialog/update-dialog';

@Injectable({ providedIn: 'root' })
export class UpdateService {
  private http = inject(HttpClient);
  private dialog = inject(MatDialog);
  private currentVersion: string | null = null;
  private dialogOpen = false;

  async init(): Promise<void> {
    this.currentVersion = await this.fetchVersion();
  }

  async checkForUpdate(): Promise<void> {
    if (this.dialogOpen) return;
    const latest = await this.fetchVersion();
    if (this.currentVersion && latest && latest !== this.currentVersion) {
      this.showUpdateDialog();
    }
  }

  private async fetchVersion(): Promise<string | null> {
    try {
      const res = await firstValueFrom(
        this.http.get<{ version: string }>('/version.json', {
          headers: { 'Cache-Control': 'no-cache', Pragma: 'no-cache' },
        })
      );
      return res.version ?? null;
    } catch {
      return null;
    }
  }

  private showUpdateDialog(): void {
    this.dialogOpen = true;
    const ref = this.dialog.open(UpdateDialogComponent, {
      disableClose: true,
      width: '320px',
    });
    ref.afterClosed().subscribe(() => {
      this.dialogOpen = false;
    });
  }
}
