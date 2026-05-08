import { Component } from '@angular/core';
import { MatDialogModule } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';

@Component({
  selector: 'app-update-dialog',
  imports: [MatDialogModule, MatButtonModule],
  template: `
    <h2 mat-dialog-title>發現新版本</h2>
    <mat-dialog-content>
      <p>應用程式已更新，請重新載入以使用最新版本。</p>
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-flat-button color="primary" (click)="reload()">立即更新</button>
    </mat-dialog-actions>
  `,
})
export class UpdateDialogComponent {
  reload(): void {
    window.location.reload();
  }
}
