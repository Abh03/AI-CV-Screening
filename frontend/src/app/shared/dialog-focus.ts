import { AfterViewInit, Directive, ElementRef, OnDestroy, inject } from '@angular/core';

const locks = new WeakMap<HTMLElement, { count: number; wasInert: boolean }>();
let openDialogs = 0;
let previousOverflow = '';

/** Keep keyboard and assistive-technology navigation inside the topmost dialog. */
@Directive({ selector: '[recruiterDialog]', standalone: true })
export class DialogFocusDirective implements AfterViewInit, OnDestroy {
  private readonly element = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly trigger = document.activeElement as HTMLElement | null;
  private readonly locked: HTMLElement[] = [];
  ngAfterViewInit(): void {
    if (openDialogs++ === 0) { previousOverflow = document.body.style.overflow; document.body.style.overflow = 'hidden'; }
    let node = this.element.nativeElement;
    while (node.parentElement && node.parentElement !== document.documentElement) {
      for (const sibling of Array.from(node.parentElement.children)) {
        if (!(sibling instanceof HTMLElement) || sibling === node || sibling.classList.contains('panel-backdrop') || sibling.tagName === 'SCRIPT') continue;
        const lock = locks.get(sibling) || { count: 0, wasInert: sibling.inert };
        lock.count++; locks.set(sibling, lock); sibling.inert = true; this.locked.push(sibling);
      }
      node = node.parentElement;
    }
    (this.element.nativeElement.querySelector<HTMLElement>('button:not([disabled]), input, select, a[href]') || this.element.nativeElement).focus();
  }
  ngOnDestroy(): void {
    for (const node of this.locked) {
      const lock = locks.get(node); if (!lock) continue;
      if (--lock.count === 0) { node.inert = lock.wasInert; locks.delete(node); }
    }
    if (--openDialogs === 0) document.body.style.overflow = previousOverflow;
    if (this.trigger?.isConnected && !this.trigger.closest('[inert]')) this.trigger.focus();
  }
}
