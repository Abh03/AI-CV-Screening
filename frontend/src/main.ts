import { bootstrapApplication } from '@angular/platform-browser';
import { provideHttpClient, withInterceptors, withXhr } from '@angular/common/http';
import { provideRouter } from '@angular/router';
import { AppComponent, routes } from './app/app';
import { httpErrorInterceptor } from './app/core/http-errors';
import { csrfInterceptor } from './app/core/session';

bootstrapApplication(AppComponent, { providers: [provideRouter(routes), provideHttpClient(withXhr(), withInterceptors([csrfInterceptor, httpErrorInterceptor]))] });
