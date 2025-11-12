import * as Bootstrap from 'ojs/ojbootstrap';
import * as Context from 'ojs/ojcontext';
import ModuleUtils = require('ojs/ojmodule-element-utils');
import * as ko from 'knockout';
import 'ojs/ojknockout';
import 'ojs/ojmodule';
import 'ojs/ojmessages';
import 'ojs/ojformlayout';
import 'ojs/ojinputtext';
import 'ojs/ojtextarea';
import 'ojs/ojbutton';
import 'ojs/ojprogress-circle';
import 'ojs/ojbindfor';

class RootViewModel {
  constructor() {
    this.moduleConfig = ko.observable({});

    ModuleUtils.createConfig({
      name: 'app',
      viewPath: 'text!js/views/',
      viewModelPath: 'js/viewModels/'
    }).then((config) => this.moduleConfig(config));
  }
}

Bootstrap.whenDocumentReady().then(() => {
  Context.getPageContext().getBusyContext().applicationBootstrapCompleted();
  const root = new RootViewModel();
  ko.applyBindings(root, document.getElementById('globalBody'));
});
