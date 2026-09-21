<?php

/*
 * Copyright (C) 2026 Abdelmonem Awad <eg2@live.com>
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice,
 *    this list of conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form must reproduce the above copyright
 *    notice, this list of conditions and the following disclaimer in the
 *    documentation and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
 * INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
 * AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
 * AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
 * OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
 * SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
 * INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
 * CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
 * ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
 * POSSIBILITY OF SUCH DAMAGE.
 */

namespace OPNsense\LinkHealth;

class IndexController extends \OPNsense\Base\IndexController
{
    public function indexAction()
    {
        /*
         * One form file describes two things: the settings shown on the page, and the dialog that
         * edits a row of the port override grid. They have to live together because the plugin
         * ships a single form, and they are told apart by the prefix of the field name - a row of
         * an array field is posted back under the key of that row, so every field of the dialog is
         * named "port.something" while the settings are named after their place in the model.
         */
        $form = $this->getForm('general');
        $generalForm = $form;
        $portForm = $form;
        /*
         * The framework always emits one leading section with no header of its own; it holds any
         * field written before the first <header>, and it is the row where base_form and
         * base_dialog hang the "full help" and "advanced mode" switches. Each of the two forms
         * needs one of its own, or whichever of them went without would render its fields with no
         * way to turn the help on.
         */
        $generalForm['sections'] = [$form['sections'][0]];
        $portForm['sections'] = [['children' => [], 'type' => false]];
        foreach (array_slice($form['sections'], 1) as $section) {
            $first = count($section['children']) ? $section['children'][0] : [];
            if (!empty($first['id']) && strpos($first['id'], 'port.') === 0) {
                $portForm['sections'][] = $section;
            } else {
                $generalForm['sections'][] = $section;
            }
        }

        $this->view->generalForm = $generalForm;
        $this->view->portForm = $portForm;
        $this->view->formGridPort = $this->getFormGrid('general', 'port', 'port');
        $this->view->pick('OPNsense/LinkHealth/index');
    }
}
